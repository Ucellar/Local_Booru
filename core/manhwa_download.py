"""(v5.44.60) «Скачать манхву»: a title from a site (``core/manhwa_sources.py``) into the manhwa folder — its free
chapters now, the new ones every night.

The owner (4.11.2026): «Скачивание — по ссылке»; «Команда переводчиков — с бóльшим числом оценок»; «Платные главы —
никогда … когда станет бесплатной: тогда и скачать»; «Новые главы — проверка раз в сутки ночью, качать сразу; был
выключен — при запуске программы».  A manga (the site says «Манга», «Западный комикс») goes to the manga folder —
it is turned page by page there; the rest («Манхва», «Маньхуа», «Рукомикс»…) to the manhwa folder, read as a strip.

The folder of a title:

* ``info.json`` — the title as the site describes it (``TitleRef.info``), ``cover.<ext>`` — its cover;
* a chapter — a folder «Том 1 Глава 7 - Имя» with its pictures ``001.jpg``… and ``chapter.json`` (``tome``,
  ``chapter``, ``name``, ``pages`` [{file, w, h}] — the strip never jumps —, ``source`` {site, id, url});
* ``.lb/source.json`` — where it came from and what is followed: ``site``, ``key``, ``url``, ``branch``, ``follow``,
  ``waiting`` (the paid chapters and when they become free), ``checked_at``; ``.lb/partial/`` — the chapters being
  downloaded (a chapter appears in the library only whole: its folder is moved in when every picture is there; a
  stopped one goes on from its pictures next time).  Both libraries leave ``.lb`` alone.

What was checked last — ``manhwa_watch.json`` in the settings folder (``checked_at``): the nightly round
(``background_duties._manhwa`` → :func:`due`) runs between 3 and 8 at night once a day, and at any hour when the
last one is more than a day old (the computer was off at night).

(v5.44.62) Chapters from other sites — the owner (4.11.2026), of the chapters Remanga sells and MangaBuff has free:
«стоило бы брать от туда если не доступно с реманги».  When the title's own site (the «home» one, of the link) lacks
something — a paid chapter, no chapters at all, or the title still comes out — the other sites are asked in the
owner's order (Remanga, ReComics, MangaLib, MangaBuff; :func:`helpers_for`): the same title found by its names
(``manhwa_sources.find_same``), its chapters met with the home site's (:func:`meet` — by tome and number, or by the
number alone when one of them counts the tomes its own way; not at all when they count the chapters otherwise).  The
plan (:func:`plan`): the home site's free chapters from it; a chapter paid there or not there at all — from the first
of the others that has it free (never a paid one of theirs); the folder names it as the home site would.  The found
ones are kept in ``source.json`` (``helpers``, ``helpers_at`` — the sites where it was not found are asked again a
week later; ``helpers_on`` — the owner's choice, the box «брать с других сайтов» on the find card and on the title's
page); a chapter of another site keeps its own site in ``chapter.json``.  A home site's free chapter that does not
come is taken from another site that has it.  A chapter once there is not taken again when its home site frees it.

(v5.44.72) The owner (5.11.2026): «описание и информация берётся из сайта, который стоит в строке скачивания, а сами
главы уже независимо от сайта, который вставил, качаются».  So: the link's site that lets no program in (a check for
robots the program window's browser does not get past, or no window — ``manhwa_sources.Closed``) — the title is the
one the name in its address is on the sites the program reads (``manhwa_link_names.find_by_words``), said on the card
(``Found.notes``); the link's site that gives the title but not its chapters (404, 403, a check) — its chapters are
looked for on the other sites all the same.

(v5.44.73) The owner: «платная глава / нет главы = ищет на других сайтах», «поставил ссылку на ремангу и мне выдал все
главы которые есть на сайтах которые я тебе давал» — com-x.life among them (through the window's browser:
``manhwa_sources.ComX``).  A paid chapter with no date to become free (Remanga's licensed titles) waits for another
site, not for a date — the words say so plainly.

(v5.44.74) The owner: «можно было бы сделать скачивание и красивее и с паузой и если глава выдала ошибку перепроверить 3
раза», «посмотри как сделано скачивание видео».  The chapters go through ``core/manhwa_run.py`` («Пауза», a chapter that
failed asked again, the meter of the page); the page picks the chapters (``download(chapters=…)``); the new ones it
left unticked are kept in ``source.json`` (``skipped``) — the nightly round does not take them (it takes the chapters
that come out later, and the paid ones that become free); the downloads one at a time with a queue, kept between runs
of the program — ``core/manhwa_queue.py``.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from core import manhwa_run as R, manhwa_sources as S

_LOG = logging.getLogger("local_booru.manhwa_get")

SOURCE_FILE = "source.json"
LB_DIR = ".lb"
PARTIAL = "partial"
WATCH_FILE = "manhwa_watch.json"
TASK = "manhwa_download"             # the page task of downloads (core/page_jobs): the page's and the nightly round's
PAGE_WORKERS = 4                     # pictures of one chapter fetched at once
NAME_MAX = 100
MANGA_TYPES = {"манга", "западный комикс", "комикс западный", "комикс"}   # turned page by page: the manga folder
NIGHT = (3, 8)                       # the hours (local) of the nightly round
AGAIN_AT_NIGHT = 18 * 3600           # a round at night when the last one is older than this
AGAIN_ANY_TIME = 24 * 3600           # …at any hour, older than this
TYPES = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
         "image/avif": ".avif", "image/bmp": ".bmp"}
RESEARCH_AFTER = 7 * 24 * 3600       # (v5.44.62) a site where the title was not found is searched again a week later
COVER_TIMEOUT = (6, 12)              # (v5.44.93) a title's cover in «Найти»: (connect, read) seconds, asked once
MEET = 0.8                           # (v5.44.62) the share of the chapters two sites must agree on


class DownloadError(RuntimeError):
    """What stops a download, in words for the page."""


# ── names ─────────────────────────────────────────────────────────────────────────────────────────────────────────
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


def safe_name(text, limit: int = NAME_MAX) -> str:
    """A name Windows takes for a folder: no ``<>:"/\\|?*``, no dots or spaces at the ends, not ``CON``…"""
    text = re.sub(r"\s+", " ", _BAD.sub(" ", str(text or ""))).strip(" .")
    if len(text) > limit:
        text = text[:limit].rstrip(" .")
        cut = text.rfind(" ")
        if cut > limit // 2:
            text = text[:cut].rstrip(" .")
    if text and text.split(".")[0].lower() in _RESERVED:
        text = "_" + text
    return text


def chapter_name(ch: S.ChapterRef) -> str:
    """The folder of a chapter, as ``manhwa_library.parse_name`` reads it: «Том 1 Глава 7 - Имя»."""
    base = (f"Том {ch.tome} " if ch.tome else "") + f"Глава {ch.number or ch.id}"
    own = safe_name(ch.name, 60)
    return safe_name(base + (f" - {own}" if own else ""), NAME_MAX + 40)


# ── where a title goes ────────────────────────────────────────────────────────────────────────────────────────────
def kind_of(title: S.TitleRef) -> str:
    """"manga" — turned page by page (the manga folder); "manhwa" — a strip."""
    return "manga" if title.type.strip().lower() in MANGA_TYPES else "manhwa"


def roots(settings: dict) -> dict:
    """{"manhwa": folder, "manga": folder or ""} — where titles go."""
    from core.manhwa_library import manhwa_root
    return {"manhwa": manhwa_root(settings), "manga": str(settings.get("manga_root") or "").strip()}


def read_source(folder: Path) -> dict:
    from core.file_safety import read_json_kept
    data = read_json_kept(Path(folder) / LB_DIR / SOURCE_FILE, dict, what="manhwa source")
    return data if isinstance(data, dict) else {}


def write_source(folder: Path, data: dict) -> None:
    from core.file_safety import write_json_kept
    write_json_kept(Path(folder) / LB_DIR / SOURCE_FILE, json.dumps(data, ensure_ascii=False, indent=1), dict)


def followed(settings: dict) -> list:
    """[(the title's folder, its source.json)] of both folders: what the nightly round looks at."""
    out = []
    for root in {r for r in roots(settings).values() if r}:
        try:
            entries = [p for p in Path(root).iterdir() if p.is_dir()]
        except OSError:
            continue
        for folder in sorted(entries, key=lambda p: p.name.lower()):
            data = read_source(folder) if (folder / LB_DIR / SOURCE_FILE).is_file() else {}
            if data.get("site") and data.get("key"):
                out.append((folder, data))
    return out


def linked_folder(settings: dict, site: str, key: str) -> "tuple[str, Path] | None":
    """(v5.44.76) (the kind, the folder) of either library whose ``source.json`` names this title — a title of the
    archive linked to a site («Из архива», ``core/manga_archive.py``) stays where it is, whatever the site calls it;
    (v5.44.84) a title of the booru parser's folder too (``core/manga_detect``: its manga found there)."""
    from core import manga_detect
    for found in manga_detect.STORE.titles(str(settings.get("root") or "")):
        folder = Path(found.path)
        if (folder / LB_DIR / SOURCE_FILE).is_file():
            data = read_source(folder)
            if data.get("site") == site and data.get("key") == key:
                return (data.get("kind") if data.get("kind") in ("manga", "manhwa") else "manga"), folder
    for kind, root in roots(settings).items():
        if not root:
            continue
        try:
            entries = [p for p in Path(root).iterdir() if p.is_dir()]
        except OSError:
            continue
        for folder in entries:
            if (folder / LB_DIR / SOURCE_FILE).is_file():
                data = read_source(folder)
                if data.get("site") == site and data.get("key") == key:
                    return kind, folder
    return None


def folder_for(root: Path, title: S.TitleRef) -> Path:
    """The folder this title has (its ``source.json`` names this site and title), else a new one by its name."""
    root = Path(root)
    base = safe_name(title.title) or safe_name(title.key) or "Без названия"
    try:
        entries = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        entries = []
    for folder in entries:
        if (folder / LB_DIR / SOURCE_FILE).is_file():
            data = read_source(folder)
            if data.get("site") == title.site and data.get("key") == title.key:
                return folder
    folder, n = root / base, 2
    while folder.exists() and read_source(folder).get("key") not in (None, title.key):
        folder = root / f"{base} ({n})"
        n += 1
    return folder


ARCHIVE_EXTS = (".zip", ".cbz")


def had(folder: Path, site: str) -> dict:
    """What the folder already has: {("id", site id) or ("n", tome, number): its folder}; (v5.44.76) a chapter's
    .zip/.cbz too, and a tome whole («Том 3.cbz», «Berserk v03», a tome's folder of pictures) — ("t", tome): every
    chapter of it is there (the titles of the archive the program did not download — ``core/manga_archive.py``)."""
    from core.manhwa_library import parse_name
    out: dict = {}
    folder = Path(folder)
    if not folder.is_dir():
        return out

    def note(path: Path) -> None:
        info = {}
        if path.is_dir() and (path / "chapter.json").is_file():
            try:
                info = json.loads((path / "chapter.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
        source = info.get("source") if isinstance(info.get("source"), dict) else {}
        if source.get("site") == site and source.get("id"):
            out[("id", str(source["id"]))] = path
        tome, number, _own = parse_name(path.name)
        tome = int(info.get("tome") or tome or 0)
        number = S._num(info.get("chapter") or number)
        if number:
            out[("n", tome, number)] = path
            out[("v", number)] = path                     # (v5.44.62) by the number alone: Found.is_had
        elif tome:
            out[("t", tome)] = path                       # (v5.44.76) a whole tome
    try:
        for sub in folder.iterdir():
            if sub.is_file():
                if sub.suffix.lower() in ARCHIVE_EXTS:   # (v5.44.76) a chapter or a tome as an archive
                    note(sub)
                continue
            if not sub.is_dir() or sub.name == LB_DIR:
                continue
            if any(p.is_dir() or p.suffix.lower() in ARCHIVE_EXTS for p in sub.iterdir()) and not any(
                    p.suffix.lower() in TYPES.values() for p in sub.iterdir() if p.is_file()):
                for inner in sub.iterdir():                      # a tome's folder with chapters inside
                    if inner.is_dir() or inner.suffix.lower() in ARCHIVE_EXTS:
                        note(inner)
            else:
                note(sub)
    except OSError:
        pass
    return out


def is_had(have: dict, ch: S.ChapterRef, by_number: bool = False) -> bool:
    """The folder has this chapter: by its site's id, by its tome and number — (v5.44.62) or by the number alone
    (``by_number``: the title counts its chapters through the tomes, so a number is one chapter); (v5.44.76) or its
    whole tome is there."""
    return ("id", ch.id) in have or (bool(ch.number) and (("n", ch.tome, ch.number) in have or
                                                            by_number and ("v", ch.number) in have)) or \
        (bool(ch.tome) and ("t", ch.tome) in have)


# ── (v5.44.62) chapters from other sites ──────────────────────────────────────────────────────────────────────────
@dataclass
class Helper:
    """Another site with the same title: its free chapters fill in what the home site has not free."""
    source: S.Source
    title: S.TitleRef
    chapters: list
    mode: str                            # "tn" — they meet by tome and number; "n" — by the number alone

    def key(self, ch: S.ChapterRef) -> tuple:
        return (ch.tome, ch.number) if self.mode == "tn" else (ch.number,)


@dataclass
class Take:
    """A chapter of the plan: ``ch`` — as the title's folder names it (the home site's tome and number), ``ref`` — the
    chapter asked of ``source`` (the home site, or another one: ``helped``); ``source`` None — nowhere free (it waits).
    ``alt`` — a home site's free chapter: another site's copy, should its own not come; (v5.44.77) a chapter of another
    site: the next site's copy (ReadManga lists the chapters it has taken down — they come only from another site)."""
    ch: S.ChapterRef
    source: "S.Source | None"
    title: S.TitleRef
    ref: S.ChapterRef
    helped: bool = False
    alt: "Take | None" = None

    @property
    def label(self) -> str:
        return self.ch.label

    @property
    def id(self) -> str:
        return self.ch.id

    @property
    def name(self) -> str:
        return self.ch.name or self.ref.name


def meet(home: list, other: list) -> str:
    """How the chapters of two sites meet: "tn" — by tome and number, "n" — by the number alone (one site counts the
    tomes its own way; each counts its chapters through the tomes), "" — they do not (one counts the chapters anew in
    each tome, the other not).  The home site with no chapters: "tn" — the other's own tomes and numbers."""
    if not home:
        return "tn"
    h = {(c.tome, c.number) for c in home if c.number}
    o = {(c.tome, c.number) for c in other if c.number}
    if h and o and len(h & o) >= max(1, round(MEET * min(len(h), len(o)))):
        return "tn"
    hn = [c.number for c in home if c.number]
    on = [c.number for c in other if c.number]
    if hn and on and len(set(hn)) == len(hn) and len(set(on)) == len(on) and \
            len(set(hn) & set(on)) >= max(1, round(MEET * min(len(hn), len(on)))):
        return "n"
    return ""


def _tome_for(number: str, chapters: list) -> int:
    """The tome the home site would give a chapter it has not: that of its chapter just before (or of its first)."""
    at = S._number_key(number)
    before = [c for c in chapters if S._number_key(c.number) <= at]
    pick = max(before, key=lambda c: (S._number_key(c.number), c.tome)) if before else (chapters[0] if chapters else None)
    return pick.tome if pick else 0


def plan(title: S.TitleRef, home: S.Source, chapters: list, helpers: list) -> list:
    """Every chapter of the title and where it comes from (:class:`Take`), in the order of tomes and numbers: the home
    site's free ones from it; one paid there, or not there at all, from the first other site (the owner's order) that
    has it free; one nobody has free waits (``source`` None)."""
    free = [(h, {h.key(c): c for c in h.chapters if c.free and c.number}) for h in helpers]
    takes = []
    for ch in chapters:
        offers = [(h, mine[h.key(ch)]) for h, mine in free if ch.number and h.key(ch) in mine]
        if ch.free:
            alt = None
            if offers:
                helper, ref = offers[0]
                alt = Take(replace(ch, name=ch.name or ref.name), helper.source, helper.title, ref, True)
            takes.append(Take(ch, home, title, ch, alt=alt))
        elif offers:
            (helper, ref), more = offers[0], offers[1:2]
            named = replace(ch, free=True, free_at="", name=ch.name or ref.name)
            alt = Take(named, more[0][0].source, more[0][0].title, more[0][1], helped=True) if more else None
            takes.append(Take(named, helper.source, helper.title, ref, helped=True, alt=alt))
        else:
            takes.append(Take(ch, None, title, ch))
    home_tn = {(c.tome, c.number) for c in chapters}
    home_n = {c.number for c in chapters}
    slots: dict = {key: None for key in home_tn}       # (tome, number) → the take of another site that fills it
    for helper, mine in free:
        for ref in sorted(mine.values(), key=lambda c: (c.tome, S._number_key(c.number))):
            if helper.mode == "tn":
                if (ref.tome, ref.number) in home_tn:
                    continue
                tome = ref.tome
            else:
                if ref.number in home_n:
                    continue
                tome = _tome_for(ref.number, chapters)
            if (tome, ref.number) in slots:
                first = slots[(tome, ref.number)]
                if first is not None and first.alt is None and first.source is not helper.source:
                    first.alt = Take(first.ch, helper.source, helper.title, ref, helped=True)   # (v5.44.77)
                continue
            ch = S.ChapterRef(id=f"{helper.source.name}:{ref.id}", tome=tome, number=ref.number, name=ref.name,
                              date=ref.date, url=ref.url)
            slots[(tome, ref.number)] = Take(ch, helper.source, helper.title, ref, helped=True)
            takes.append(slots[(tome, ref.number)])
    order = {id(t): n for n, t in enumerate(takes)}
    takes.sort(key=lambda t: (t.ch.tome, S._number_key(t.ch.number), order[id(t)]))
    return takes


def worth_asking(title: S.TitleRef, chapters: list) -> bool:
    """The other sites may have what the home site lacks: it has no chapters, or a paid one, or the title still comes
    out (a finished one, all free, has nothing more to come)."""
    if not chapters or any(not c.free for c in chapters):
        return True
    return not re.match(r"(?i)\s*(заверш|законч|выпуск прекращ)", title.status or "")


LEGACY_SEARCHED = ("remanga", "recomics", "mangalib", "mangabuff")   # (v5.44.73) what a search before 5.44.73 asked


def _team_names(team: str) -> set:
    return {S.name_key(x) for x in str(team or "").split(",") if S.name_key(x)}


def helper_branch(source: S.Source, other: S.TitleRef, team: str = "", language: str = "ru") -> "S.Branch | None":
    """(v5.44.79) The branch of another site the chapters are taken from: on a site of many languages (MangaDex) — the
    title's language, else none; on a site of translators — the home site's own translators when they are there too
    (the owner, of his friend's reader: «искало именно того перевода который тебе надо»), else the most voted."""
    if getattr(source, "languages", False):
        return next((b for b in other.branches if b.id == (language or "ru")), None)
    mine = _team_names(team)
    if mine:
        same = [b for b in other.branches if b.id and mine & _team_names(b.name)]
        if same:
            return max(same, key=lambda b: (b.votes, b.count))
    return other.best_branch()


def helpers_for(net: S.Net, home: S.Source, title: S.TitleRef, chapters: list, stored: dict, *, say=None,
                team: str = "") -> tuple:
    """([:class:`Helper`], what ``source.json`` keeps of them): the other sites in the owner's order — the ones
    ``source.json`` names asked straight, the rest searched (``find_same``) a week after the last search at most;
    (v5.44.73) a site not searched yet (``helpers_sites`` — com-x.life, added to a title searched before) — at once;
    (v5.44.79) their chapters of the home site's translators (``team``) when they have them, in the title's language
    (a title of MangaDex in English or in its original language takes nothing from the Russian sites)."""
    say = say or (lambda _text: None)
    known = {str(h.get("site")): h for h in stored.get("helpers") or [] if isinstance(h, dict) and h.get("key")}
    searched_at = float(stored.get("helpers_at") or 0)
    search = _now() - searched_at >= RESEARCH_AFTER
    searched = set(stored.get("helpers_sites") or (LEGACY_SEARCHED if searched_at else ()))
    asked: set = set()
    out, keep = [], []
    language = title.language or "ru"
    if language != "ru":                               # (v5.44.79) the other sites are Russian: nothing asked, and
        return [], {"helpers": list(stored.get("helpers") or []), "helpers_at": int(searched_at),   # what was kept
                    "helpers_sites": sorted(stored.get("helpers_sites") or [])}                      # stays as it was
    for other_site in S.readable():
        if other_site.name == home.name or other_site.adult_only and not title.is_erotic:
            continue                                   # (v5.44.85) a site of hentai — only for an erotic title
        if getattr(home, "adult_only", False) and not other_site.adult_only:
            continue                                   # (v5.44.93) a work of a site of hentai — on those alone: the
            #                                            other eight sites searched one by one for a work of Хентай-тян
            #                                            held its card 146 s (the owner: «даже не попытался загрузится»)
        if other_site.SIGNED_ONLY and not other_site.signed_in():
            continue                                   # (v5.44.96) HentaiLib without a sign-in gives no pages: not a
            #                                            helper (not searched either — searched once there is one)
        entry = known.get(other_site.name)
        if entry is None and not search and other_site.name in searched:
            continue
        try:
            if entry is not None:
                other = other_site.title(net, str(entry["key"]))
            else:
                say(f"Ищу этот тайтл на {other_site.label}")
                other = S.find_same(net, other_site, title)
                asked.add(other_site.name)
                if other is None:
                    say(f"{other_site.label}: этого тайтла там нет")
                    continue
            best = helper_branch(other_site, other, team, language)
            if best is None and getattr(other_site, "languages", False):
                say(f"{other_site.label}: «{other.title}» там есть, но не на нужном языке")
                continue
            theirs = other_site.chapters(net, other, best.id if best else "")
        except S.Missing as exc:
            say(f"{other_site.label}: тайтла там больше нет ({exc})")
            continue
        except S.SourceError as exc:
            say(f"{other_site.label}: не ответил — {exc}")
            if entry is not None:
                keep.append(entry)                     # kept: the site did not answer this time
            continue
        mode = meet(chapters, theirs)
        if not mode:
            say(f"{other_site.label}: «{other.title}» — главы там считаются иначе, оттуда не беру")
            continue
        out.append(Helper(other_site, other, theirs, mode))
        keep.append({"site": other_site.name, "key": other.key, "url": other.url, "title": other.title})
        if entry is None:
            say(f"{other_site.label}: нашёлся «{other.title}» — глав {len(theirs)}")
    sites = (set() if search else searched) | asked | {str(h["site"]) for h in keep}
    return out, {"helpers": keep, "helpers_at": int(_now()) if search else int(searched_at),
                 "helpers_sites": sorted(sites)}


# ── what the page shows before downloading ────────────────────────────────────────────────────────────────────────
@dataclass
class Found:
    title: S.TitleRef
    branch: "S.Branch | None"
    chapters: list
    kind: str                            # "manhwa" / "manga"
    folder: str
    have: dict = field(default_factory=dict)
    cover: bytes = b""
    cover_type: str = ""
    helpers: list = field(default_factory=list)          # (v5.44.62) Helper: the other sites with this title
    helpers_on: bool = True                              # the owner's choice for this title (source.json)
    helper_record: dict = field(default_factory=dict)    # what source.json keeps of them
    asked_helpers: bool = False                          # the other sites were asked
    notes: list = field(default_factory=list)            # (v5.44.72) what the card says first (the link's site…)
    asked: str = ""                                      # (v5.44.72) the link's site, when the title is another's
    about: "S.TitleRef | None" = None                    # (v5.44.112) the title on the site its description is from
    about_site: str = ""                                 # …that site's name (``about_of``; "" — the home site's own)

    def by_number(self) -> bool:
        numbers = [c.number for c in self.chapters if c.number]
        return bool(numbers) and len(set(numbers)) == len(numbers)

    def is_had(self, ch: S.ChapterRef) -> bool:
        return is_had(self.have, ch, by_number=self.by_number())

    def takes(self, helpers: "bool | None" = None) -> list:
        """The plan (:func:`plan`) — with the other sites (``helpers``; the owner's choice of the title unless said)."""
        use = self.helpers_on if helpers is None else bool(helpers)
        return plan(self.title, S.by_name(self.title.site), self.chapters, self.helpers if use else [])

    def free(self) -> list:
        return [c for c in self.chapters if c.free]

    def paid(self) -> list:
        return [c for c in self.chapters if not c.free]

    def new(self, helpers: "bool | None" = None) -> list:
        """What a download takes (:class:`Take`): the chapters the folder has not that some site has free."""
        return [t for t in self.takes(helpers) if t.source is not None and not self.is_had(t.ch)]

    def waiting(self, helpers: "bool | None" = None) -> list:
        """The paid chapters no site has free (and the folder has not): they wait for their dates."""
        return [t.ch for t in self.takes(helpers) if t.source is None and not self.is_had(t.ch)]

    def tiles(self, takes: list) -> list:
        """(v5.44.74) Every chapter of the plan with the other sites (``takes``) for its tile on the page: id, tome,
        number, name, label; have — in the folder; wait — no site has it free; from — the other site it comes from;
        paid — its own site sells it (``free_at`` — when it becomes free there)."""
        sold = {c.id: c.free_at for c in self.chapters if not c.free}
        return [{"id": t.id, "tome": t.ch.tome, "n": t.ch.number, "name": t.name, "label": t.label,
                 "have": self.is_had(t.ch), "wait": t.source is None,
                 "from": t.source.label if t.helped and t.source is not None else "",
                 "paid": t.id in sold, "free_at": _date(sold.get(t.id, ""))} for t in takes]

    def public(self) -> dict:
        takes = self.takes(True)
        paid = [c for c in self.paid() if not self.is_had(c)]
        helped = [t for t in takes if t.helped and not self.is_had(t.ch)]
        covered = {t.ch.id: t.source.label for t in takes if t.helped}
        left = [c for c in paid if c.id not in covered]
        helpers = []
        for h in self.helpers:
            mine = [t for t in helped if t.title.site == h.title.site]
            helpers.append({"site": h.source.name, "site_label": h.source.label, "title": h.title.title,
                            "url": h.title.url, "gives": len(mine), "first": mine[0].label if mine else "",
                            "last": mine[-1].label if mine else ""})
        info = self.about or self.title                  # (v5.44.112) the description and the facts: Remanga's first
        return {
            "site": self.title.site, "site_label": getattr(S.by_name(self.title.site), "label", self.title.site),
            "key": self.title.key, "url": self.title.url, "title": info.title or self.title.title,
            "site_title": self.title.title,
            "alt": (info.alt_titles or self.title.alt_titles)[:3], "type": info.type or self.title.type,
            "status": info.status or self.title.status, "year": info.year or self.title.year or "",
            "age": info.age or self.title.age, "erotic": self.title.is_erotic or info.is_erotic,
            "genres": (info.genres or self.title.genres)[:8], "about": (info.description or self.title.description)[:700],
            "about_site": getattr(S.by_name(self.about.site), "label", self.about.site) if self.about else "",
            "branches": [{"id": b.id, "name": b.name, "votes": b.votes, "count": b.count}
                         for b in self.title.branches if b.id],
            "branch": self.branch.id if self.branch else "",
            "languages": bool(getattr(S.by_name(self.title.site), "languages", False)),     # (v5.44.79) MangaDex
            "chapters": len(self.chapters), "free": len(self.free()), "new": len(self.new(False)),
            "have": len([c for c in self.chapters if self.is_had(c)]),
            "paid": [{"label": c.label, "free_at": _date(c.free_at), "from": covered.get(c.id, "")} for c in paid[:30]],
            "paid_count": len(paid), "paid_left": len(left),
            "waiting": [{"label": c.label, "free_at": _date(c.free_at)} for c in left[:5]],
            "helped": len(helped), "helped_paid": len([t for t in helped if t.ch.id in {c.id for c in paid}]),
            "helpers": helpers, "helpers_on": self.helpers_on, "helpers_asked": self.asked_helpers,
            "licensed": self.title.licensed, "notes": list(self.notes), "asked": self.asked,
            "kind": self.kind, "folder": self.folder, "cover": bool(self.cover),
            "first": self.chapters[0].label if self.chapters else "", "last": self.chapters[-1].label if self.chapters else "",
            "updated": _date(self.chapters[-1].date) if self.chapters else "",      # (v5.44.87) the newest one's date
            "list": self.tiles(takes),
        }


def _date(text: str) -> str:
    """«2025-02-02T00:00:00» → «02.02.2025»."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)", str(text or ""))
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else str(text or "")


def look(url: str, settings: dict, *, branch: str = "", net: "S.Net | None" = None, where: str = "",
         cover: bool = True, helpers: bool = True, stored: "dict | None" = None, say=None, about: bool = True,
         name: str = "") -> Found:
    """The title of a link, the chapters of its branch (the most voted, unless ``branch``) and what is already there;
    its cover too (``cover``), unless the folder has one already; (v5.44.62) the other sites with this title
    (``helpers``, when worth asking — :func:`worth_asking`; ``stored`` — the title's ``source.json``, else read from
    its folder); (v5.44.112) the description and the facts from the first site in the owner's order that has them
    (``about`` — :func:`about_of`).  A link whose site says it has no such title (404 — «Атака Титанов» of MangaLib, the
    owner: «MangaLib ответил 404»), lets no program in, or would not give it — the title by the name in the link on the
    other sites (:func:`_by_link_name`; ``name`` — the title's name as the page that gave the link said it, a tile
    of «Лента»: asked first): the chapters from the site that has it."""
    source, key = S.source_for(url)
    if source is None:
        raise DownloadError(UNKNOWN_LINK)
    net = net or S.Net()
    say = say or (lambda _text: None)
    notes, asked = [], ""
    try:
        title = source.title(net, key)
    except (S.Closed, S.Refused, S.Missing) as exc:   # (v5.44.72) the link's site lets no program in, or not to this
        asked = source.label                            # title; (v5.44.112) or has it no more (404)
        source, title, note = _by_link_name(net, source, key, exc, say, name=name)
        notes.append(note)
    except S.SourceError as exc:
        raise DownloadError(str(exc)) from exc
    if source.SIGNED_ONLY and not source.signed_in():
        notes.append(source.sign_in_words())        # (v5.44.96) HentaiLib: the list without, pages with
    chosen = next((b for b in title.branches if b.id and b.id == str(branch)), None) or title.best_branch()
    if getattr(source, "languages", False):       # (v5.44.79) MangaDex: the branch is the chapters' language
        title.language = chosen.id if chosen else ""
    try:
        chapters = source.chapters(net, title, chosen.id if chosen else "")
    except (S.Missing, S.Refused, S.Closed) as exc:
        # (v5.44.72) the title is there, its chapters are not given: they are looked for on the other sites all the same
        chapters = []
        notes.append(f"{source.label} не дал список глав ({exc})" + (" — главы ищутся на других сайтах" if helpers
                                                                     else ""))
        say(notes[-1])
    except S.SourceError as exc:
        raise DownloadError(str(exc)) from exc
    kind = where if where in ("manhwa", "manga") else kind_of(title)
    root = roots(settings).get(kind) or ""
    if kind == "manga" and not root:
        kind, root = "manhwa", roots(settings)["manhwa"]
    linked = linked_folder(settings, title.site, title.key)
    if linked is not None and where in ("", linked[0]):     # (v5.44.76) a folder already its: there, either library
        kind, folder = linked
    else:
        folder = folder_for(Path(root), title) if root else Path(safe_name(title.title))
    picture, picture_type = b"", ""
    if title.cover_url and cover and not any((folder / f"cover{ext}").is_file() for ext in set(TYPES.values())):
        try:
            # (v5.44.94–95) short, once, on each of its hosts (Хентай-тян's mirrors: ``S.picture_links``): its picture
            # host hung 40 s three times — the card waited minutes for a cover (the owner: «даже не попытался
            # загрузится»); no cover — the card has its letter
            picture, picture_type = _cover_of(net, source, title)
            if not _picture_type(picture, picture_type):
                picture, picture_type = b"", ""
        except S.SourceError as exc:
            _LOG.info("cover of %s not taken: %s", title.url, exc)
    elif cover:                                       # the folder's own cover: the page shows it
        for ext in set(TYPES.values()):
            if (folder / f"cover{ext}").is_file():
                try:
                    picture, picture_type = (folder / f"cover{ext}").read_bytes(), ""
                except OSError:
                    pass
                break
    if stored is None:
        stored = read_source(folder) if (folder / LB_DIR / SOURCE_FILE).is_file() else {}
    found = Found(title=title, branch=chosen, chapters=chapters, kind=kind, folder=str(folder),
                  have=had(folder, title.site), cover=picture, cover_type=picture_type,
                  helpers_on=bool(stored.get("helpers_on", True)),
                  helper_record={k: stored[k] for k in ("helpers", "helpers_at", "helpers_sites") if k in stored},
                  notes=notes, asked=asked)
    if helpers and worth_asking(title, chapters):
        paid = [c for c in chapters if not c.free]
        if paid:
            say(f"Платных на {source.label}: {len(paid)} — ищу их на других сайтах")
        team = chosen.name if chosen and not getattr(source, "languages", False) else ""
        found.helpers, found.helper_record = helpers_for(net, source, title, chapters, stored, say=say, team=team)
        found.asked_helpers = True
    if about:
        try:
            got = about_of(net, source, title, found.helpers, say=say)
        except Exception as exc:  # noqa: BLE001 - the description of another site is a nicety: the home's stays
            _LOG.info("the description of %s from another site: %s", title.url, exc)
            got = None
        if got is not None:
            found.about, found.about_site = got[1], got[0].name
    return found


ABOUT_SEARCHES = 3              # (v5.44.112) sites searched at most for a title's description
# (v5.44.112) what of ``info.json`` comes from the site of the description (the name, the facts, the description)
ABOUT_KEYS = ("title", "alt_titles", "type", "status", "year", "genres", "tags", "description", "authors")


def _searchable() -> tuple:
    """The sites a search by name asks (``manhwa_search.ASKED``: not com-x.life and MangaDex — only by a link)."""
    from core.manhwa_search import ASKED
    return ASKED


def about_of(net: S.Net, source: S.Source, title: S.TitleRef, helpers: list, *, say=None) -> "tuple | None":
    """(v5.44.112) (site, its title) whose description and facts the card and the title's page show — the owner: «само
    описание и все все все про тайтл с ре манги или если там нет то по очереди пока не будет»: the first site in his
    order (``S.readable``: Remanga first) that has this title with a description; the home site's own when it comes
    first (None); a site found among the helpers is not searched again; the sites a search by name does not ask
    (com-x.life, MangaDex — :func:`_searchable`) — only as helpers; :data:`ABOUT_SEARCHES` searches at most; a site of hentai only for an erotic title, and a title of
    a site of hentai only on those."""
    say = say or (lambda _text: None)
    if (title.language or "ru") != "ru":             # (v5.44.79) a title of MangaDex in English or in its original
        return None                                  # language: the Russian sites are not asked — its own description
    searched = 0
    for site in S.readable():
        if site.name == source.name:
            if str(title.description or "").strip():
                return None
            continue
        if site.adult_only and not title.is_erotic or getattr(source, "adult_only", False) and not site.adult_only:
            continue
        helper = next((h for h in helpers if h.source.name == site.name), None)
        if helper is not None:
            other = helper.title
        elif site.name not in _searchable() or searched >= ABOUT_SEARCHES:
            continue
        else:
            searched += 1
            say(f"Ищу описание на {site.label}")
            try:
                other = S.find_same(net, site, title)
            except S.SourceError as exc:
                say(f"{site.label}: не ответил — {exc}")
                continue
        if other is not None and str(other.description or "").strip():
            say(f"Описание — с {site.label}")
            return site, other
    return None


def _cover_of(net: S.Net, source: S.Source, title: S.TitleRef) -> tuple:
    """(bytes, type) of a title's cover — :class:`manhwa_sources.SourceError` when none of its hosts gave it."""
    first = None
    for link in S.picture_links(title.cover_url):
        try:
            got = net.fetch(link, limit=8 * 1024 * 1024, what=source.label, timeout=COVER_TIMEOUT, tries=1,
                            headers={"Referer": source.referer(title), "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
            S.gave_picture(link)
            return got
        except S.Closed:
            raise
        except S.SourceError as exc:
            first = first or exc
    raise first or S.SourceError(f"{source.label}: обложка не пришла")


UNKNOWN_LINK = ("Эту ссылку программа не знает: нужна страница тайтла на Remanga, ReComics, MangaLib, MangaBuff, "
                "ReadManga, Desu, com-x.life, MangaDex, HentaiLib, AllHentai или Хентай-тян")


def _by_link_name(net: S.Net, source: S.Source, key: str, why: S.SourceError, say, *, name: str = "") -> tuple:
    """(v5.44.72) (the site, its title, the card's words) of a link whose site lets no program in (or would not give
    this title): the title the name in its address is on the sites the program reads
    (``manhwa_link_names.find_by_words``) — :class:`DownloadError` with words when it is on none of them; (v5.44.112)
    ``name`` — the title's name the page of the link gave (a tile of «Лента»: «Атака Титанов») — asked first."""
    # (v5.44.85) a title of a site of hentai — on the other sites of hentai alone, another one's — never on them
    sites = [s for s in S.readable() if s.name != source.name and s.adult_only == source.adult_only]
    names = ", ".join(s.label for s in sites)
    reason = str(why) if isinstance(why, S.Closed) else f"{source.label} не дал этот тайтл ({why})"
    if not sites:
        raise DownloadError(f"{reason}.")
    words = source.words(key)
    named = [w.lower() for w in re.findall(r"[^\W_]+", str(name or ""))][:12]
    if not words and not named:
        raise DownloadError(f"{reason}. В ссылке нет названия тайтла — вставьте ссылку на этот тайтл с {names}")
    said = " ".join(words)
    from core.manhwa_link_names import find_by_words
    found = None
    if named and named != words:                      # (v5.44.112) the name the tile said — the sites know it best
        say(f"{reason}. Ищу этот тайтл по названию «{name}» на {names}")
        found = find_by_words(net, named, sites, say=say)
    if found is None and words:
        say(f"{reason}. Ищу этот тайтл по названию из ссылки («{said}») на {names}")
        found = find_by_words(net, words, sites, say=say)
    if found is None:
        asked = " и ".join(f"«{x}»" for x in dict.fromkeys(x for x in (str(name or "").strip(), said) if x))
        raise DownloadError(f"{reason}. По названию ({asked}) тайтл не нашёлся ни на одном из сайтов, "
                            f"которые программа читает ({names}). Если он там есть — вставьте ссылку оттуда.")
    other, title, _share = found
    say(f"{other.label}: нашёлся «{title.title}» — описание и главы оттуда и с других сайтов")
    return other, title, (f"{reason}. Поэтому тайтл найден на {other.label} по названию — описание оттуда, "
                          f"главы — оттуда и с других сайтов. Проверьте, тот ли это тайтл.")


# ── downloading ───────────────────────────────────────────────────────────────────────────────────────────────────
# (v5.44.74) how it goes — «Пауза», the chapters asked again, the meter of the page — is core/manhwa_run.py's
pause, resume, paused = R.pause, R.resume, R.paused


def _picture_type(data: bytes, ctype: str = "") -> str:
    """The extension of a picture by its first bytes (the type the site said only when they say nothing)."""
    head = bytes(data[:16])
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    if head.startswith(b"BM"):
        return ".bmp"
    return ""


def _size_of(path: Path) -> tuple:
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except Exception:            # noqa: BLE001 - a picture PIL cannot read: its size unknown, the strip still shows it
        return (0, 0)


def _now() -> float:
    return time.time()


def _iso_ready(free_at: str) -> bool:
    """A paid chapter's date has come (its site says it is free from then)."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d))?", str(free_at or ""))
    if not m:
        return False
    when = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), int(m.group(5) or 0),
                    tzinfo=timezone.utc)
    return when.timestamp() <= _now()


def _chapter(net: S.Net, take: Take, folder: Path, k: int, halt) -> str:
    """One chapter into ``folder``: its pictures into ``.lb/partial/<name>`` (another site's — ``<name> (<site>)``: two
    sites' pictures never mix), then the whole folder moved in.  The chapter's folder (its name in the title's
    folder), or "" when ``halt()`` stopped it half-way («Остановить», (v5.44.74) «Пауза» — its pictures there stay)."""
    source, title, ch, ref = take.source, take.title, take.ch, take.ref
    name = chapter_name(ch)
    base = folder / LB_DIR / PARTIAL
    partial = base / (f"{name} ({title.site})" if take.helped else name)
    partial.mkdir(parents=True, exist_ok=True)
    pictures = source.pictures(net, title, ref)
    R.item(k, state="going", pages=len(pictures), got=0)
    referer = source.referer(title)
    done: dict = {}
    lock = threading.Lock()

    def came(n: int) -> None:
        with lock:
            done[n] = True
            R.item(k, got=len(done))

    def one(n: int, pic: S.Picture):
        for old in partial.glob(f"{n:03d}.*"):
            if old.suffix.lower() in TYPES.values() and old.stat().st_size > 0:
                came(n)
                return n, old                                    # downloaded before a stop or a pause
        if halt():
            return n, None
        data, ctype = S.fetch_picture(net, pic, what=source.label,     # (v5.44.82) its other servers too
                                      headers={"Referer": referer, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
        ext = _picture_type(data, ctype)
        if not ext:
            raise S.SourceError(f"{source.label} прислал не картинку вместо страницы {n}")
        path = partial / f"{n:03d}{ext}"
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        R.add_bytes(len(data))
        came(n)
        return n, path

    files: dict = {}
    with ThreadPoolExecutor(max_workers=PAGE_WORKERS, thread_name_prefix="manhwa-page") as pool:
        for n, path in pool.map(lambda pair: one(*pair), enumerate(pictures, 1)):
            files[n] = path
    if halt() or any(path is None for path in files.values()):
        return ""
    pages = []
    for n, pic in enumerate(pictures, 1):
        path = files[n]
        w, h = (pic.width, pic.height) if pic.width > 0 and pic.height > 0 else _size_of(path)
        pages.append({"file": path.name, "w": int(w), "h": int(h)})
    info = {"tome": ch.tome or None, "chapter": ch.number, "name": ch.name, "pages": pages,
            "source": {"site": title.site, "id": ref.id, "url": ref.url}, "downloaded_at": int(_now())}
    (partial / "chapter.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    target, n = folder / name, 2
    while target.exists():
        if is_had(had(folder, title.site), ch):
            shutil.rmtree(partial, ignore_errors=True)            # the same chapter came meanwhile
            return target.name
        target = folder / f"{name} ({n})"
        n += 1
    os.replace(partial, target)
    for other in [base / name, *(Path(x) for x in glob.glob(glob.escape(str(base / name)) + " (*)"))]:
        shutil.rmtree(other, ignore_errors=True)         # (v5.44.62) the same chapter begun from another site
    R.item(k, got=len(pages))
    return target.name


def _write_title(net: S.Net, source: S.Source, found: Found, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    info_path = folder / "info.json"
    info = found.title.info()
    for helper in found.helpers:                     # (v5.44.62) what the home site does not say, another one may
        theirs = helper.title.info()
        for key in ("description", "genres", "tags", "authors", "year", "alt_titles"):
            if info.get(key) in (None, "", []) and theirs.get(key) not in (None, "", []):
                info[key] = theirs[key]
    if found.about is not None:                      # (v5.44.112) the description and the facts: the owner's first
        theirs = found.about.info()                  # site that has them (Remanga first — ``about_of``)
        for key in ABOUT_KEYS:
            if theirs.get(key) not in (None, "", []):
                info[key] = theirs[key]
        info["is_erotic"] = bool(info.get("is_erotic") or theirs.get("is_erotic"))
        info["info_site"] = found.about.site
    try:
        old = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    except (OSError, ValueError):
        old = {}
    if isinstance(old, dict) and read_source(folder).get("archive"):
        # (v5.44.76) a title of the archive («Из архива»): what its own description says stays, the site's fills in
        info = {**{k: v for k, v in info.items() if v not in (None, "", [])},
                **{k: v for k, v in old.items() if v not in (None, "", [])}}
    elif isinstance(old, dict):
        fresh = {k: v for k, v in info.items() if v not in (None, "", [])}
        if found.about is None and old.get("info_site") and old.get("info_site") != found.title.site:
            fresh = {k: v for k, v in fresh.items() if k not in ABOUT_KEYS}   # (v5.44.112) the other site's stays
        info = {**old, **fresh}
    tmp = info_path.with_suffix(".json.part")
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, info_path)
    has_cover = any((folder / f"cover{ext}").is_file() for ext in set(TYPES.values()))
    if found.cover and not has_cover:
        ext = _picture_type(found.cover, found.cover_type) or ".jpg"
        (folder / f"cover{ext}").write_bytes(found.cover)


def download(found: Found, *, chapters: list | None = None, follow: bool = True, helpers: "bool | None" = None,
             net: "S.Net | None" = None, say=None, stop_check=None, start_paused: bool = False) -> dict:
    """The chapters of ``found`` that the folder has not and some site has free (or ``chapters`` — their ids: the ones
    the page ticked) into the title's folder — (v5.44.62) from the other sites too (``helpers``; the title's own choice
    unless said); the paid ones nobody has free are kept in ``.lb/source.json`` with their dates; (v5.44.74) the new
    ones left out of ``chapters`` — as ``skipped`` (the nightly round leaves them).  «Пауза», the chapters asked
    again, the meter — ``core/manhwa_run.py``; ``start_paused`` — begun on «Пауза» (one the program closed on).
    A result for the page: the chapters that did not come — ``failed_chapters`` [{label, why}] (not «failed»: a
    page task takes a result's «failed» for a refusal of the whole task — until 5.44.74 a download with one chapter
    that did not come ended «прервано ошибкой» with that list as its words)."""
    say = say or (lambda _text: None)
    stop_check = stop_check or (lambda: False)
    net = net or S.Net(stop_check)
    use = found.helpers_on if helpers is None else bool(helpers)
    source = S.by_name(found.title.site)
    folder = Path(found.folder)
    try:                     # (v5.44.107) the folder as it is now: a download that waited in the queue was found before
        found.have = had(folder, found.title.site)     # the one ahead of it put chapters there (perhaps of this title)
    except OSError as exc:
        _LOG.info("the folder %s not read again before its download: %s", folder, exc)
    new = found.new(use)
    chosen = None if chapters is None else {str(c) for c in chapters}
    wanted = [t for t in new if chosen is None or t.id in chosen]
    locked = [t for t in wanted if needs_sign_in(t)]    # (v5.44.96) HentaiLib's without a sign-in: not asked
    wanted = [t for t in wanted if not needs_sign_in(t)]
    paid = found.waiting(use)
    R.begin(found.title.title, found.title.site, str(folder), wanted, kind=found.kind)
    _write_title(net, source, found, folder)
    stored = read_source(folder)
    record = {**stored, "site": found.title.site, "key": found.title.key, "url": found.title.url,
              "branch": found.branch.id if found.branch else "", "branch_name": found.branch.name if found.branch else "",
              "follow": bool(follow), "kind": found.kind, "title": found.title.title,
              "added_at": stored.get("added_at") or int(_now()),
              "waiting": [{"id": c.id, "label": c.label, "free_at": c.free_at} for c in paid],
              **found.helper_record, "helpers_on": use, "licensed": found.title.licensed,
              "skipped": sorted(t.id for t in new if chosen is not None and t.id not in chosen)}
    if not record["skipped"]:
        record.pop("skipped")
    write_source(folder, record)
    if paid and not any(c.free_at for c in paid):
        say(f"Платных глав, которых пока нет бесплатно ни на одном из сайтов: {len(paid)} — программа их не покупает; "
            "появятся бесплатно — скачает сама")
    elif paid:
        say(f"Платных глав: {len(paid)} — программа их не скачивает; станут бесплатными — скачает сама "
            f"(ближайшая — {paid[0].label}, {_date(paid[0].free_at) or 'дата не названа'})")
    helped = [t for t in wanted if t.helped]
    if helped:
        sites = ", ".join(dict.fromkeys(t.source.label for t in helped))
        say(f"С других сайтов ({sites}) — глав: {len(helped)}, тех, которых нет бесплатно на {source.label}")
    if record.get("skipped"):
        say(f"Не отмечено: {len(record['skipped'])} — их программа сама не скачивает (ночью — только новые главы)")
    if locked:
        say(f"{locked[0].source.sign_in_words()} — глав ждут входа: {len(locked)}")
    elif not wanted:
        say("Новых бесплатных глав нет — всё уже в папке" if not new else "Глав к скачиванию не отмечено")
    if start_paused:                               # paused when the program closed: paused again
        R.pause()
        say("ПАУЗА с прошлого запуска — «Продолжить», чтобы качать дальше")
    try:                                           # «Пауза»: the strips on their way come, the next ones wait
        run = R.go(wanted, lambda take, k, halt: _chapter(net, take, folder, k, halt), say=say, stop_check=stop_check)
    finally:
        R.resume()
    got = [name for _k, name, _take in run["got"]]
    others = sum(1 for _k, _name, take in run["got"] if take.helped)
    record["checked_at"] = int(_now())
    write_source(folder, record)
    _clean_partial(folder)
    if record.get("archive"):                      # (v5.44.76) a title of the archive: «Из архива» shows it as it is now
        try:
            from core import manga_archive
            manga_archive.note_download(folder, found)
        except (OSError, ValueError) as exc:
            _LOG.warning("the archive's counts of %s not noted: %s", folder, exc)
    failed = run["failed"] + [{"label": take.label, "why": take.source.sign_in_words()}
                              for take in locked]
    result = {"title": found.title.title, "folder": str(folder), "kind": found.kind, "got": len(got),
              "chapters": got, "failed_chapters": failed, "paid": len(paid), "stopped": run["stopped"],
              "helped": others,
              "gave_up": run["gave_up"], "skipped": len(record.get("skipped") or [])}
    R.values(finished=True, gave_up=run["gave_up"])
    return result


def needs_sign_in(take: "Take") -> bool:
    """(v5.44.96) A chapter only a site that gives its pages to a sign-in alone has, and the program has no sign-in
    there (HentaiLib without the owner's MangaLib): asking would bring nothing."""
    options = [take] + ([take.alt] if take.alt is not None else [])
    return all(o.source is not None and o.source.SIGNED_ONLY and not o.source.signed_in() for o in options)


def _clean_partial(folder: Path) -> None:
    """Empty folders of chapters being downloaded go (a stopped chapter's pictures stay for the next time)."""
    base = folder / LB_DIR / PARTIAL
    try:
        for sub in base.iterdir():
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        if base.is_dir() and not any(base.iterdir()):
            base.rmdir()
    except OSError:
        pass


# ── every night ───────────────────────────────────────────────────────────────────────────────────────────────────
def _watch_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / WATCH_FILE


def last_round() -> float:
    try:
        return float(json.loads(_watch_file().read_text(encoding="utf-8")).get("checked_at") or 0)
    except (OSError, ValueError, AttributeError):
        return 0.0


def due(now: float | None = None, last: float | None = None) -> bool:
    """Time for the round of new chapters: at night once a day, any time when the last one is a day old."""
    now = _now() if now is None else now
    last = last_round() if last is None else last
    if now - last >= AGAIN_ANY_TIME:
        return True
    hour = time.localtime(now).tm_hour
    return NIGHT[0] <= hour < NIGHT[1] and now - last >= AGAIN_AT_NIGHT


def watch_round(settings: dict, *, say=None, stop_check=None, net: "S.Net | None" = None) -> dict:
    """Every followed title: its site asked for chapters, the new free ones (and the paid ones that became free)
    downloaded.  Kept as done in ``manhwa_watch.json`` unless stopped."""
    say = say or (lambda _text: None)
    stop_check = stop_check or (lambda: False)
    net = net or S.Net(stop_check)
    titles = [(f, d) for f, d in followed(settings) if d.get("follow", True)]
    if not titles:
        say("Следить не за чем: тайтлов, скачанных программой, нет")
    total, problems, stopped = 0, [], False
    for folder, data in titles:
        if stop_check():
            stopped = True
            break
        name = data.get("title") or folder.name
        try:
            found = look(data.get("url") or "", settings, branch=str(data.get("branch") or ""), net=net,
                         where=str(data.get("kind") or ""), cover=False, stored=data, say=say,
                         helpers=bool(data.get("helpers_on", True)), about=False)
        except DownloadError as exc:
            problems.append(f"{name}: {exc}")
            say(f"{name}: сайт не ответил — {exc}")
            continue
        if Path(found.folder) != folder:              # the folder was renamed by hand: it stays where it is
            found.folder = str(folder)
            found.have = had(folder, found.title.site)
        skipped = {str(x) for x in data.get("skipped") or []}     # (v5.44.74) left unticked on the page: not taken
        new = [t for t in found.new() if t.id not in skipped]
        if not new:
            data["checked_at"] = int(_now())
            data["waiting"] = [{"id": c.id, "label": c.label, "free_at": c.free_at} for c in found.waiting()]
            data["licensed"] = found.title.licensed
            data.update(found.helper_record)
            write_source(folder, data)
            continue
        say(f"{name}: новых глав — {len(new)}")
        result = download(found, chapters=[t.id for t in new] if skipped else None, follow=bool(data.get("follow", True)),
                          net=net, say=say, stop_check=stop_check)
        total += result["got"]
        if result.get("stopped"):
            stopped = True
            break
    if not stopped:
        from core.file_safety import write_json_kept
        write_json_kept(_watch_file(), json.dumps({"checked_at": int(_now()), "got": total, "titles": len(titles)}),
                        dict)
    if total:
        say(f"Новых глав скачано: {total}")
    return {"titles": len(titles), "got": total, "problems": problems, "stopped": stopped}
