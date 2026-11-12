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
last one is more than a day old (the computer was off at night).  (v5.44.116) «Остановить» of the round means «until
the next night»: a round the person stopped is kept as checked too (only one the program's closing cut short is not —
the next start goes on with it); «Настройки → Сайты манги → Новые главы — скачивать самой» (``manhwa_watch_auto``)
off — no round at all.

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

(v5.44.117) The owner: «открыл тайтл и все разом начали грузить и не приходилось одному сайту ждать другой».  The other
sites — the ones the chapters are looked for on (:func:`helpers_for`) and the ones the description is (:func:`about_of`;
a site asked for its chapters is not asked again: one answer serves both) — and the cover are asked at once, each in a
thread of its own (:func:`_ask_all`, :class:`_Others`), :data:`ASK_DEADLINE` seconds at most all together; what they
give is decided in the owner's order (``SOURCES``), so it does not depend on who answered first.  The page is shown the
link's site's chapters at once and the rest as it comes (``look(shown=)``: ``Found.asking`` — the sites still asked).

(v5.44.146) The owner (10.11.2026), of «Возвращение героя катастрофы» — 171 chapters ReadManga has taken down, every one
of them on com-x.life: «readmanga вроде находит но не качает … но почему оно не пытается найти на других а просто
сразу в ошибку кидает».  Two things were wrong.  A chapter had one other site in reserve — now every site that has it
free, each the next one's ``alt`` (:func:`plan`).  And a site that had not answered within :data:`ASK_DEADLINE` when
the title was found (com-x.life goes through the window's browser, Manga One Love was slow that night) was out of
the download altogether — now the download asks such sites once more itself (``download`` → ``rescue``,
:data:`RESCUE_DEADLINE`): when a chapter has come from none of the sites that had it, and — «скачать все» — when the
chapters it began with are done and some still wait for a free site.  What they have joins the plan at once: the
chapter is asked there, the ones that waited are added.  A download that still could not get its chapters says where
they were looked for (:func:`_looked`).

(v5.44.163) The owner (12.11.2026), two lines of «Готово»: «Не скачалось ни одной главы из 16: 5 глав подряд не дал
ни один сайт» — under them five sites with every chapter free.  The sites cut one title into tomes each its own way
(measured on his computer: one ends tome 3 at chapter 152, another at 168, a third keeps everything in tome 1), and a
site whose tomes agreed with the home site's for most chapters was met by tome and number alone — so its chapters of
the last tome, where the two differ, were «chapters the home site has not»: takes of their own, of that one site
(the others' copies of the very same chapters stood beside them under another tome), first in the order — the site
withheld five of them in a row and the download gave up before any other site was asked.  Now a title whose home
site counts its chapters through the tomes (:func:`counted_through` — a number is one chapter) is met by the number
wherever the tomes differ (:func:`plan`, :func:`meet`): one take a chapter, every site that has it its option, the
tome — the home site's; and five chapters in a row that did not come stop only the chapters that have no other site
(``manhwa_run.go``).  The same day, of the processor: the round of new chapters only while nobody is at the computer
(:func:`watch_round` ``here``, ``manhwa_run.wait_away``) — «Продолжить» of «Загрузки» lets it go on at once.
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
ASK_DEADLINE = 45.0                  # (v5.44.117) seconds the other sites of a title, asked at once, are waited for
QUIET_DEADLINE = 600.0               # (v5.44.117) the same when no page waits for the answer (the night round, the
                                     # queue, «Из архива»): as before — a slow site (com-x through its check) is not skipped
RESCUE_DEADLINE = 180.0              # (v5.44.146) …and when a download asks the sites that did not answer once more
TRUST_SEARCH = 600.0                 # (v5.44.146) seconds a search's «тайтла там нет» is not asked again by a download
# (v5.44.146) how the searches of the other sites are kept (``source.json`` «helpers_rule»): until now a site that had not
# answered its search was kept as searched — «тайтла там нет» for a week.  What an earlier rule kept is searched anew once
HELPERS_RULE = 2


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


def follow_choice(stored: dict) -> bool:
    """(v5.44.116) «Новые главы — скачивать самой» of a title as its card and its page tick it: the owner's choice
    (``follow`` of source.json) once the program has downloaded it (``added_at``), else on — a title never downloaded,
    a folder of the archive only linked (its «follow» is off until its first download)."""
    return bool(stored.get("follow", True)) if stored.get("added_at") else True


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
    archive linked to a site («Из архива», ``core/manga_archive.py``) stays where it is, whatever the site calls it.
    (v5.44.84–5.44.124 a title of the booru parser's folder too, linked where it lay; v5.44.125 — never: the program
    writes nothing into the parser's folder, such a title is downloaded into as its copy in «Манга» —
    ``core/parser_manga``.)"""
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
def counted_through(chapters: list) -> bool:
    """(v5.44.163) The site counts these chapters through the tomes: no number is in two tomes, so a number is one
    chapter (a chapter listed twice in one tome is one all the same); a list with no numbers — not."""
    places = {(c.tome, c.number) for c in chapters if c.number}
    return bool(places) and len({number for _tome, number in places}) == len(places)


def one_chapter_numbers(chapters: list) -> set:
    """(v5.44.163) The numbers that are one chapter of the list: in one tome only."""
    tomes: dict = {}
    for c in chapters:
        if c.number:
            tomes.setdefault(c.number, set()).add(c.tome)
    return {number for number, found in tomes.items() if len(found) == 1}


@dataclass
class Helper:
    """Another site with the same title: its free chapters fill in what the home site has not free."""
    source: S.Source
    title: S.TitleRef
    chapters: list
    mode: str                            # how its chapters were found to be the home site's (:func:`meet`): "tn" —
    #                                      their tomes and numbers agree; "n" — the numbers alone (the tomes differ)

    def reach(self, through: bool) -> tuple:
        """(v5.44.163) Its free chapters as the plan looks them up: ({(tome, number): the chapter}, {number: the
        chapter} — the numbers that are one chapter here, when the home site counts through the tomes
        (``through``); else nothing by the number alone: a number is a chapter of each tome)."""
        free = [c for c in self.chapters if c.free and c.number]
        by_place = {(c.tome, c.number): c for c in free}
        if not through:
            return by_place, {}
        single = one_chapter_numbers(self.chapters)
        return by_place, {c.number: c for c in free if c.number in single}


@dataclass
class Take:
    """A chapter of the plan: ``ch`` — as the title's folder names it (the home site's tome and number), ``ref`` — the
    chapter asked of ``source`` (the home site, or another one: ``helped``); ``source`` None — nowhere free (it waits).
    ``alt`` — a home site's free chapter: another site's copy, should its own not come; (v5.44.77) a chapter of another
    site: the next site's copy (ReadManga lists the chapters it has taken down — they come only from another site);
    (v5.44.146) that copy's own ``alt`` — the site after it, and so on: every site that has the chapter free
    (``manhwa_run.options_of``)."""
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
    tomes its own way; the home site counts its chapters through the tomes), "" — they do not (one counts the chapters
    anew in each tome, the other not).  The home site with no chapters: "tn" — the other's own tomes and numbers.
    (v5.44.163) "n" does not ask any more that no number of the other site be in two tomes (a chapter uploaded
    twice, an extra «0» of every tome — and the whole site was «counted otherwise»): such a number meets by its tome
    alone, the rest by their numbers — as :func:`plan` looks them up."""
    if not home:
        return "tn"
    h = {(c.tome, c.number) for c in home if c.number}
    o = {(c.tome, c.number) for c in other if c.number}
    if not h or not o:
        return ""
    need = max(1, round(MEET * min(len(h), len(o))))
    if len(h & o) >= need:
        return "tn"
    if counted_through(home):
        single = one_chapter_numbers(other)
        if sum(1 for place in h if place in o or place[1] in single) >= need:
            return "n"
    return ""


def numbering(chapters: list, helpers: list) -> list:
    """(v5.44.163) The chapters a title's tomes and numbers are taken from: the home site's; when it lists none (its
    chapters come from the other sites alone) — those of the first other site that lists any."""
    if chapters:
        return chapters
    return next((h.chapters for h in helpers if h.chapters), [])


def _tome_for(number: str, chapters: list) -> int:
    """The tome the home site would give a chapter it has not: that of its chapter just before (or of its first)."""
    at = S._number_key(number)
    before = [c for c in chapters if S._number_key(c.number) <= at]
    pick = max(before, key=lambda c: (S._number_key(c.number), c.tome)) if before else (chapters[0] if chapters else None)
    return pick.tome if pick else 0


def _chained(options: list) -> "Take | None":
    """(v5.44.146) The takes of one chapter — one a site, in the order they are asked — each the ``alt`` of the one
    before it; the first of them."""
    for one, following in zip(options, options[1:]):
        one.alt = following
    return options[0] if options else None


def plan(title: S.TitleRef, home: S.Source, chapters: list, helpers: list) -> list:
    """Every chapter of the title and where it comes from (:class:`Take`), in the order of tomes and numbers: the home
    site's free ones from it; one paid there, or not there at all, from the first other site (the owner's order) that
    has it free; one nobody has free waits (``source`` None).  (v5.44.146) Every other site that has the chapter free
    is its take's ``alt``, each the next one's — a chapter is asked of all of them, not of two.
    (v5.44.163) Whose chapter is whose: by tome and number — and, in a title counted through the tomes
    (:func:`counted_through` of :func:`numbering`), by the number wherever the tomes differ (the sites cut one title
    into tomes each its own way), when that number is one chapter of the other site (``Helper.reach``).  A chapter
    the home site has not is then one take whatever tome its sites put it in, named with the tome the home site is
    at there (:func:`_tome_for`) — before, a site that agreed with the home site on most tomes brought its last tome
    as chapters of its own, beside the other sites' copies of the same ones (and first in the order)."""
    base = numbering(chapters, helpers)
    if not chapters:                     # the home site lists none: the first site's own count — the others met with it
        helpers = [h for h in helpers if h.chapters is base or meet(base, h.chapters)]
    through = counted_through(base)
    reach = [(h, *h.reach(through)) for h in helpers]
    takes = []
    for ch in chapters:
        offers = []
        for helper, by_place, by_number in reach:
            ref = (by_place.get((ch.tome, ch.number)) or by_number.get(ch.number)) if ch.number else None
            if ref is not None:
                offers.append((helper, ref))
        offers.sort(key=lambda o: S.withheld(o[0].source.label))   # (v5.44.136) a site that gives the program no
        if ch.free:                                                 # pictures lately — after the others
            copies = [Take(replace(ch, name=ch.name or ref.name), helper.source, helper.title, ref, True)
                      for helper, ref in offers]
            takes.append(_chained([Take(ch, home, title, ch), *copies]))
        elif offers:
            named = replace(ch, free=True, free_at="", name=ch.name or offers[0][1].name)
            takes.append(_chained([Take(named, helper.source, helper.title, ref, helped=True)
                                   for helper, ref in offers]))
        else:
            takes.append(Take(ch, None, title, ch))
    home_places = {(c.tome, c.number) for c in chapters}
    home_numbers = {c.number for c in chapters}
    slots: dict = {}                     # the place of a chapter the home site has not → the first site's take of it
    for helper, by_place, by_number in reach:
        for ref in sorted(by_place.values(), key=lambda c: (c.tome, S._number_key(c.number))):
            if through:                  # a number is one chapter: its place is its number, whatever the site's tome
                if ref.number in home_numbers or by_number.get(ref.number) is not ref:
                    continue             # the home site's own chapter — or a number this site has in two tomes
                slot, tome = ref.number, _tome_for(ref.number, base)
            else:                        # a number is a chapter of each tome: its place — the site's tome and number
                if (ref.tome, ref.number) in home_places:
                    continue
                slot, tome = (ref.tome, ref.number), ref.tome
            first = slots.get(slot)
            if first is not None:
                copies = R.options_of(first)             # (v5.44.77) the next site's copy, (v5.44.146) every site's
                if all(one.source is not helper.source for one in copies):
                    copies[-1].alt = Take(first.ch, helper.source, helper.title, ref, helped=True)
                continue
            ch = S.ChapterRef(id=f"{helper.source.name}:{ref.id}", tome=tome, number=ref.number, name=ref.name,
                              date=ref.date, url=ref.url)
            slots[slot] = Take(ch, helper.source, helper.title, ref, helped=True)
            takes.append(slots[slot])
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


def _kept_record(stored: dict) -> dict:
    """What ``source.json`` keeps of the other sites, as it was (none asked: a title not in Russian)."""
    return {"helpers": list(stored.get("helpers") or []), "helpers_at": int(float(stored.get("helpers_at") or 0)),
            "helpers_sites": sorted(stored.get("helpers_sites") or []),
            **({"helpers_rule": stored["helpers_rule"]} if "helpers_rule" in stored else {})}


@dataclass
class _Plan:
    """(v5.44.117) Which other sites :func:`helpers_for` asks of a title: [(site, its entry in ``source.json`` — asked by
    its key — or None: searched)] in the owner's order; whether a search is due (a week after the last); the sites
    searched before and when."""
    sites: list
    search: bool
    searched: set
    searched_at: float
    rule: int = 1                        # (v5.44.146) the rule what is kept was searched by (:data:`HELPERS_RULE`)


def _helper_plan(home: S.Source, title: S.TitleRef, stored: dict) -> "_Plan | None":
    """The other sites in the owner's order — the ones ``source.json`` names asked straight, the rest searched
    (``find_same``) a week after the last search at most; (v5.44.73) a site not searched yet (``helpers_sites`` —
    com-x.life, added to a title searched before) — at once.  None — a title not in Russian: nothing asked."""
    known = {str(h.get("site")): h for h in stored.get("helpers") or [] if isinstance(h, dict) and h.get("key")}
    searched_at = float(stored.get("helpers_at") or 0)
    try:
        rule = int(stored.get("helpers_rule") or 1)
    except (TypeError, ValueError):
        rule = 1
    # (v5.44.146) searched by an earlier rule: once anew (a site that had not answered was kept as «not there»)
    search = _now() - searched_at >= RESEARCH_AFTER or rule < HELPERS_RULE
    searched = set(stored.get("helpers_sites") or (LEGACY_SEARCHED if searched_at else ()))
    if (title.language or "ru") != "ru":               # (v5.44.79) the other sites are Russian: nothing asked, and
        return None                                    # what was kept stays as it was
    sites = []
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
        sites.append((other_site, entry))
    return _Plan(sites, search, searched, searched_at, rule)


@dataclass
class _Asked:
    """(v5.44.117) What one other site answered of a title — the same answer for its chapters (:func:`helpers_for`) and
    for its description (:func:`about_of`): the site is asked once."""
    site: S.Source
    entry: "dict | None" = None          # ``source.json`` names its title there: asked by its key, not searched
    chapters: bool = True                # its chapters too (a helper's), not only its title (the description's)
    done: bool = False                   # it has answered (or has failed)
    other: "S.TitleRef | None" = None    # its title (None — not there, or no answer)
    helper: "Helper | None" = None       # its chapters met the home site's
    searched: bool = False               # its search answered this time (the title found there or not)
    failed: bool = False                 # it did not answer
    late: bool = False                   # …not within the time of all of them (``_ask_all``)


def _ask(net: S.Net, asked: _Asked, title: S.TitleRef, chapters: list, *, team: str, say, lock) -> None:
    """One other site asked of ``title`` (in a thread of its own: :func:`_ask_all`): its title — by the key
    ``source.json`` keeps, else by its search (``find_same``) — and (a helper) its chapters of the right translators or
    language, met with the home site's (:func:`meet`).  What it said goes into ``asked`` under ``lock`` at once."""
    site = asked.site
    other = helper = None
    searched = failed = False
    language = title.language or "ru"
    try:
        if asked.entry is not None:
            other = site.title(net, str(asked.entry["key"]))
        else:
            say(f"Ищу этот тайтл на {site.label}")
            other = S.find_same(net, site, title)
            searched = True
            if other is None:
                say(f"{site.label}: этого тайтла там нет")
        if other is not None and asked.chapters:
            best = helper_branch(site, other, team, language)
            if best is None and getattr(site, "languages", False):
                say(f"{site.label}: «{other.title}» там есть, но не на нужном языке")
            else:
                theirs = site.chapters(net, other, best.id if best else "")
                mode = meet(chapters, theirs)
                if not mode:
                    say(f"{site.label}: «{other.title}» — главы там считаются иначе, оттуда не беру")
                else:
                    helper = Helper(site, other, theirs, mode)
                    if asked.entry is None:
                        say(f"{site.label}: нашёлся «{other.title}» — глав {len(theirs)}")
    except S.Missing as exc:                           # (its title, when it came, still tells its description)
        say(f"{site.label}: тайтла там больше нет ({exc})")
        helper = None
    except S.SourceError as exc:
        say(f"{site.label}: не ответил — {exc}")
        helper, failed = None, True
    except Exception as exc:  # noqa: BLE001 - one site's surprise: the others are taken all the same
        _LOG.warning("%s: asked of %s: %s: %s", site.label, title.url, type(exc).__name__, exc)
        say(f"{site.label}: ответил непонятно")
        helper, failed = None, True
    with lock:
        if asked.done:                                 # too late: its time was over, it counts as not answered
            return
        asked.other, asked.helper, asked.searched, asked.failed = other, helper, searched, failed
        asked.done = True


def _ask_all(net: S.Net, asking: list, title: S.TitleRef, chapters: list, *, team: str, say, deadline: float,
             answered=None) -> bool:
    """(v5.44.117) The other sites of ``asking`` (:class:`_Asked`) asked at once, each in a thread of its own (the owner:
    «все разом начали грузить и не приходилось одному сайту ждать другой»), all of them waited for ``deadline`` seconds
    together; ``answered()`` — each time one has answered (under no lock: it reads ``asking`` itself).  The ones still
    asking then stop (their net's stop: ``Net.asked_until``) and count as not answered.  -> all of them answered."""
    lock = threading.Lock()
    came = threading.Event()
    over = threading.Event()             # their time is over: a site still asked says nothing more (its words would
    started = time.monotonic()           # come in the middle of what follows — the next title of the night's round)
    late = lambda: net.stop_check() or time.monotonic() - started > deadline     # noqa: E731
    shared = net.asked_until(late)

    def words(text: str) -> None:
        if not over.is_set():
            say(text)

    def one(asked: _Asked) -> None:
        try:
            _ask(shared, asked, title, chapters, team=team, say=words, lock=lock)
        finally:
            with lock:
                if not asked.done:                     # (it did not get as far as saying anything)
                    asked.failed, asked.done = True, True
            came.set()
    for asked in asking:          # threads of their own (daemons): one that hangs on a site holds nobody at the exit
        threading.Thread(target=one, args=(asked,), name=f"manhwa-ask-{asked.site.name}", daemon=True).start()
    while True:
        with lock:
            left = [a for a in asking if not a.done]
        if not left or late():
            break
        came.wait(min(0.5, max(0.05, deadline - (time.monotonic() - started))))
        if came.is_set():
            came.clear()
            if answered is not None:
                answered()
    with lock:
        over.set()
        late_ones = [a for a in asking if not a.done]
        for asked in late_ones:                         # too late: as a site that did not answer this time
            asked.failed, asked.late, asked.helper = True, True, None
            asked.done = True
    if late_ones and not net.stop_check():
        say("Не ответили вовремя — без них: " + ", ".join(a.site.label for a in late_ones))
    return not late_ones


def _helpers_of(wanted: _Plan, asking: list) -> tuple:
    """([:class:`Helper`] in the owner's order, what ``source.json`` keeps of them) by the sites' answers — the same
    whoever answered first: a helper — a site whose chapters met; a site of ``source.json`` that did not answer — kept
    as it was; the sites searched now — not searched again for a week."""
    out, keep, searched_now = [], [], set()
    for asked in asking:
        if not asked.chapters:
            continue
        if asked.searched:
            searched_now.add(asked.site.name)
        if asked.done and asked.helper is not None:
            other = asked.helper.title
            out.append(asked.helper)
            keep.append({"site": asked.site.name, "key": other.key, "url": other.url, "title": other.title})
        elif asked.entry is not None and (asked.failed or not asked.done):
            keep.append(asked.entry)                   # kept: the site did not answer this time
    sites = (set() if wanted.search else wanted.searched) | searched_now | {str(h["site"]) for h in keep}
    return out, {"helpers": keep, "helpers_at": int(_now()) if wanted.search else int(wanted.searched_at),
                 "helpers_sites": sorted(sites), "helpers_rule": HELPERS_RULE if wanted.search else wanted.rule}


def helpers_for(net: S.Net, home: S.Source, title: S.TitleRef, chapters: list, stored: dict, *, say=None,
                team: str = "", deadline: "float | None" = None) -> tuple:
    """([:class:`Helper`], what ``source.json`` keeps of them): the other sites in the owner's order (:func:`_helper_plan`)
    — (v5.44.117) asked at once (:func:`_ask_all`), taken in that order whoever answered first;
    (v5.44.79) their chapters of the home site's translators (``team``) when they have them, in the title's language
    (a title of MangaDex in English or in its original language takes nothing from the Russian sites)."""
    say = say or (lambda _text: None)
    wanted = _helper_plan(home, title, stored)
    if wanted is None:
        return [], _kept_record(stored)
    asking = [_Asked(site, entry) for site, entry in wanted.sites]
    _ask_all(net, asking, title, chapters, team=team, say=say, deadline=ASK_DEADLINE if deadline is None else deadline)
    return _helpers_of(wanted, asking)


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
    # (v5.44.116; the review: «Новые главы — скачивать самой» was ticked on every card — a download switched it back on)
    # the owner's choice for this title (:func:`follow_choice`)
    follow_on: bool = True
    helper_record: dict = field(default_factory=dict)    # what source.json keeps of them
    asked_helpers: bool = False                          # the other sites were asked
    notes: list = field(default_factory=list)            # (v5.44.72) what the card says first (the link's site…)
    asked: str = ""                                      # (v5.44.72) the link's site, when the title is another's
    about: "S.TitleRef | None" = None                    # (v5.44.112) the title on the site its description is from
    about_site: str = ""                                 # …that site's name (``about_of``; "" — the home site's own)
    # (v5.44.117) the other sites still asked (their names — the page says «ищу на …» and shows what came so far) and
    # the ones that did not answer in time (a title found so is not kept for «уже открывали»: ``manhwa_get``)
    asking: list = field(default_factory=list)
    late: list = field(default_factory=list)
    # (v5.44.123) the covers of the sites the title was found on — its own site's and the other sites' (the menu of
    # covers: ``webui/pages/covers.py``): [{"site", "label", "key", "url", "page"}] in the owner's order; and where
    # ``cover`` came from: {"how": "folder"} — the folder's own, {"how": "site", "site", "url"}, {"how": "own", "file"}
    covers: list = field(default_factory=list)
    cover_from: dict = field(default_factory=dict)

    def by_number(self) -> bool:
        """The title is counted through the tomes: a number is one chapter (:func:`counted_through` of what its tomes
        and numbers are taken from — :func:`numbering`)."""
        return counted_through(numbering(self.chapters, self.helpers))

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
            "asking": list(self.asking), "late": list(self.late),     # (v5.44.117) the other sites, asked at once
            "follow": self.follow_on,                                  # (v5.44.116) the box «Новые главы» ticked by it
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
         name: str = "", elsewhere: bool = True, shown=None, deadline: "float | None" = None) -> Found:
    """The title of a link, the chapters of its branch (the most voted, unless ``branch``) and what is already there;
    its cover too (``cover``), unless the folder has one already; (v5.44.62) the other sites with this title
    (``helpers``, when worth asking — :func:`worth_asking`; ``stored`` — the title's ``source.json``, else read from
    its folder); (v5.44.112) the description and the facts from the first site in the owner's order that has them
    (``about`` — :func:`about_of`).  A link whose site says it has no such title (404 — «Атака Титанов» of MangaLib, the
    owner: «MangaLib ответил 404»), lets no program in, or would not give it — the title by the name in the link on the
    other sites (:func:`_by_link_name`; ``name`` — the title's name as the page that gave the link said it, a tile
    of «Лента»: asked first): the chapters from the site that has it; (v5.44.115) not when ``elsewhere`` is off — the
    night's round and the queue: a site's 404 there is said, not a title found by words taken without a person.

    (v5.44.117; the owner: «открыл тайтл и все разом начали грузить и не приходилось одному сайту ждать другой») the
    other sites — for the chapters and for the description — and the cover are asked at once (:class:`_Others`; one
    question to a site serves both), ``deadline`` seconds at most all together (:data:`ASK_DEADLINE`); what they give is
    decided in the owner's order, so it is the same whoever answered first.  ``shown(found)`` — the title as it is so
    far (a copy): first with the link's site's chapters and ``asking`` (the sites asked), then each time something
    came (a site's chapters; the description once every site before its site in the order has answered; the cover)."""
    source, key = S.source_for(url)
    if source is None:
        raise DownloadError(UNKNOWN_LINK)
    net = net or S.Net()
    say = say or (lambda _text: None)
    notes, asked = [], ""
    try:
        title = source.title(net, key)
    except (S.Closed, S.Refused, S.Missing) as exc:   # (v5.44.72) the link's site lets no program in, or not to this
        if isinstance(exc, S.Missing) and not elsewhere:     # title; (v5.44.112) or has it no more (404) — (v5.44.115)
            raise DownloadError(str(exc)) from exc           # not at night nor in the queue: no person to say «тот ли»
        asked = source.label
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
    picture, picture_type, came = b"", "", {}
    from core import title_covers as TC
    folder_cover = TC.current(folder) if cover else None
    # (v5.44.123) the owner's choice (the menu of covers) of a title whose folder is not one of the program's yet —
    # or is the booru parser's (never written into): its picture is the cover; a folder of the program's — its own
    choice = None
    if cover and not (folder_cover is not None and TC.in_library(folder, settings)):
        choice = TC.chosen([f"{title.site}:{title.key}"])
    fetch_cover = bool(cover) and choice is not None or bool(title.cover_url and cover) and folder_cover is None
    if cover and folder_cover is not None and choice is None:   # the folder's own cover: the page shows it
        try:
            picture, picture_type, came = folder_cover.read_bytes(), "", {"how": "folder"}
        except OSError:
            pass
    if stored is None:
        stored = read_source(folder) if (folder / LB_DIR / SOURCE_FILE).is_file() else {}
    found = Found(title=title, branch=chosen, chapters=chapters, kind=kind, folder=str(folder),
                  have=had(folder, title.site), cover=picture, cover_type=picture_type,
                  helpers_on=bool(stored.get("helpers_on", True)),
                  follow_on=follow_choice(stored),                     # (v5.44.116)
                  helper_record={k: stored[k] for k in ("helpers", "helpers_at", "helpers_sites", "helpers_rule")
                                 if k in stored},
                  notes=notes, asked=asked, cover_from=came, covers=_covers_of([(source, title)]))
    others = _Others(net, source, title, chapters, stored, found, say=say, shown=shown,
                     deadline=ASK_DEADLINE if deadline is None else deadline)
    others.run(helpers=helpers and worth_asking(title, chapters), about=about, fetch_cover=fetch_cover,
               team=chosen.name if chosen and not getattr(source, "languages", False) else "", choice=choice)
    return found


def _covers_of(pairs: list) -> list:
    """(v5.44.123) The covers of a title on the sites ([(site, its title there)]) for the menu of covers: one a site,
    in the owner's order, none without an address."""
    order = {s.name: n for n, s in enumerate(S.readable())}
    out, seen = [], set()
    for site, other in sorted(pairs, key=lambda p: order.get(p[0].name, len(order))):
        if other is None or not other.cover_url or site.name in seen:
            continue
        seen.add(site.name)
        out.append({"site": site.name, "label": site.label, "key": other.key, "url": other.cover_url,
                    "page": other.url})
    return out


class _Others:
    """(v5.44.117) The other sites of a title and its cover, asked at once for :func:`look`: their chapters
    (:func:`helpers_for`'s sites), the description (:func:`about_of`'s — a site asked for the chapters is not asked
    again: one answer serves both), the cover; each new thing shown to the page as it comes (``shown``)."""

    def __init__(self, net: S.Net, source: S.Source, title: S.TitleRef, chapters: list, stored: dict, found: Found, *,
                 say, shown, deadline: float):
        self.net, self.source, self.title, self.chapters = net, source, title, chapters
        self.stored, self.found, self.say, self.shown, self.deadline = stored, found, say, shown, deadline
        self.lock = threading.Lock()                  # the found as the threads change it
        self.showing = threading.Lock()               # its copies go to the page one at a time, in order
        self.ended = False                            # the look has its answer: a cover later than that is dropped
        self.wanted: "_Plan | None" = None
        self.asking: list = []
        self.choice: "dict | None" = None

    def run(self, *, helpers: bool, about: bool, fetch_cover: bool, team: str, choice: "dict | None" = None) -> None:
        found, title, say = self.found, self.title, self.say
        self.choice = choice                          # (v5.44.123) the owner's cover of a title without a folder
        if helpers:
            self.wanted = _helper_plan(self.source, title, self.stored)
            if self.wanted is None:                   # a title not in Russian: the Russian sites are not asked
                found.helper_record = _kept_record(self.stored)
            else:
                self.asking = [_Asked(site, entry) for site, entry in self.wanted.sites]
            found.asked_helpers = True
            paid = [c for c in self.chapters if not c.free]
            if paid:
                say(f"Платных на {self.source.label}: {len(paid)} — ищу их на других сайтах")
        if about:
            mine = {a.site.name for a in self.asking}
            self.asking += [_Asked(site, chapters=False) for site in _about_sites(self.source, title, mine)]
        order = {s.name: n for n, s in enumerate(S.readable())}
        self.asking.sort(key=lambda a: order.get(a.site.name, len(order)))
        found.asking = [a.site.label for a in self.asking]
        if found.asking:
            say("Спрашиваю разом: " + ", ".join(found.asking))
        cover_thread = None
        if fetch_cover:
            cover_thread = threading.Thread(target=self._cover, name="manhwa-ask-cover", daemon=True)
            cover_thread.start()
        started = time.monotonic()
        self._show()                                  # the link's site's chapters at once
        if self.asking:
            _ask_all(self.net, self.asking, title, self.chapters, team=team, say=say, deadline=self.deadline,
                     answered=self._gather)
        if cover_thread is not None:                  # (the cover asks short and once: ``COVER_TIMEOUT``)
            cover_thread.join(max(0.0, self.deadline - (time.monotonic() - started)))
        self._gather(final=True)

    def _cover(self) -> None:
        """(v5.44.94–95) short, once, on each of its hosts (Хентай-тян's mirrors: ``S.picture_links``): its picture host
        hung 40 s three times — the card waited minutes for a cover (the owner: «даже не попытался загрузится»); no
        cover — the card has its letter; (v5.44.117) asked beside the other sites, the page shows it when it comes.
        (v5.44.123) The owner chose another one for a title without a folder (the menu of covers): that one; it does
        not come — the site's."""
        came = {"how": "site", "site": self.title.site, "url": self.title.cover_url}
        got = None
        if self.choice is not None:
            from core import title_covers as TC
            got = TC.picture_of(self.choice)
            if got is not None:
                came = {k: self.choice.get(k) for k in ("how", "site", "url", "file") if self.choice.get(k)}
        if got is None and self.title.cover_url:
            try:
                got = _cover_of(self.net, self.source, self.title)
            except S.SourceError as exc:
                _LOG.info("cover of %s not taken: %s", self.title.url, exc)
                return
        if got is None:
            return
        picture, picture_type = got
        if not _picture_type(picture, picture_type):
            return
        with self.lock:
            if self.ended:                            # too late: the title has been given as it was
                return
            self.found.cover, self.found.cover_type, self.found.cover_from = picture, picture_type, came
        self._show()

    def _gather(self, final: bool = False) -> None:
        """What the sites have said so far, decided in the owner's order (so whoever answered first, the same): the
        helpers that answered (at the end — all of them, with what ``source.json`` keeps); the description once every
        site before its site has answered."""
        answers = {a.site.name: a for a in self.asking}
        with self.lock:
            found = self.found
            if self.wanted is not None:
                if final:
                    found.helpers, found.helper_record = _helpers_of(self.wanted, self.asking)
                else:
                    found.helpers = [a.helper for a in self.asking if a.chapters and a.done and a.helper is not None]
            decided, got = _about_decision(self.source, self.title, answers)
            if decided and got is not None:
                found.about, found.about_site = got[1], got[0].name
            # (v5.44.123) the covers of every site that has the title (the menu of covers): what has come so far
            found.covers = _covers_of([(self.source, self.title)] + [(a.site, a.other) for a in self.asking
                                                                     if a.done and a.other is not None])
            found.asking = [] if final else [a.site.label for a in self.asking if not a.done]
            if final:
                found.late = [a.site.label for a in self.asking if a.late]
                self.ended = True
            about = found.about_site
        if final and about:
            self.say(f"Описание — с {getattr(S.by_name(about), 'label', about)}")
        if not final:
            self._show()

    def _show(self) -> None:
        """The title as it is now — a copy — to the page (``shown``)."""
        if self.shown is None:
            return
        with self.showing:
            with self.lock:
                if self.ended:
                    return
                now = replace(self.found, helpers=list(self.found.helpers), notes=list(self.found.notes),
                              asking=list(self.found.asking), late=list(self.found.late),
                              covers=list(self.found.covers), cover_from=dict(self.found.cover_from))
            try:
                self.shown(now)
            except Exception as exc:  # noqa: BLE001 - the page's view of it: the look goes on all the same
                _LOG.warning("the title found so far not shown: %s", exc)


ABOUT_SEARCHES = 3              # (v5.44.112) sites searched at most for a title's description
# (v5.44.112) what of ``info.json`` comes from the site of the description (the name, the facts, the description)
ABOUT_KEYS = ("title", "alt_titles", "type", "status", "year", "genres", "tags", "description", "authors")


def _searchable() -> tuple:
    """The sites a search by name asks (``manhwa_search.ASKED``: not com-x.life and MangaDex — only by a link)."""
    from core.manhwa_search import ASKED
    return ASKED


def _about_sites(source: S.Source, title: S.TitleRef, asked: set) -> list:
    """(v5.44.117) The sites asked of a title for its description alone, besides the ones asked for its chapters
    (``asked`` — their answers serve the description too: a site is asked once): the sites a search by name asks
    (:func:`_searchable`), :data:`ABOUT_SEARCHES` at most, in the owner's order — the ones after the home site only
    when it has no description of its own; a site of hentai only for an erotic title, a title of a site of hentai only
    on those; a title not in Russian — none."""
    if (title.language or "ru") != "ru":             # (v5.44.79) a title of MangaDex in English or in its original
        return []                                    # language: the Russian sites are not asked — its own description
    out = []
    for site in S.readable():
        if site.name == source.name:
            if str(title.description or "").strip():
                break
            continue
        if site.adult_only and not title.is_erotic or getattr(source, "adult_only", False) and not site.adult_only:
            continue
        if site.name in asked or site.name not in _searchable():
            continue
        if len(out) >= ABOUT_SEARCHES:
            break
        out.append(site)
    return out


def _about_decision(source: S.Source, title: S.TitleRef, answers: dict) -> tuple:
    """(v5.44.117) (decided, (site, its title) or None) — the description and the facts the card shows, of the sites'
    answers so far (``answers``: the site's name → :class:`_Asked`): the first site in the owner's order with this title
    and a description, the home site's own when it comes first (None); decided only once every site before that one
    has answered — they are asked at once, and the one chosen is the same whoever answered first.  A site the search by
    name does not ask (com-x.life, MangaDex) — only when its chapters are taken (a helper), as before."""
    if (title.language or "ru") != "ru":
        return True, None
    for site in S.readable():
        if site.name == source.name:
            if str(title.description or "").strip():
                return True, None
            continue
        asked = answers.get(site.name)
        if asked is None:
            continue
        if not asked.done:
            return False, None                       # it may yet have it: the ones after it wait
        if asked.helper is not None:
            other = asked.helper.title
        elif site.name not in _searchable():
            continue
        else:
            other = asked.other
        if other is not None and str(other.description or "").strip():
            return True, (site, other)
    return True, None


def about_of(net: S.Net, source: S.Source, title: S.TitleRef, helpers: list, *, say=None) -> "tuple | None":
    """(v5.44.112) (site, its title) whose description and facts the card and the title's page show — the owner: «само
    описание и все все все про тайтл с ре манги или если там нет то по очереди пока не будет»: the first site in his
    order (``S.readable``: Remanga first) that has this title with a description; the home site's own when it comes
    first (None); a site found among the helpers is not searched again; the sites a search by name does not ask
    (com-x.life, MangaDex — :func:`_searchable`) — only as helpers; :data:`ABOUT_SEARCHES` searches at most; a site of
    hentai only for an erotic title, and a title of a site of hentai only on those.  (v5.44.117) The sites searched at
    once (:func:`_ask_all`), the one taken by the order (:func:`_about_decision`); :func:`look` asks them together with
    the sites of the chapters (:class:`_Others`)."""
    say = say or (lambda _text: None)
    answers = {h.source.name: _Asked(h.source, done=True, other=h.title, helper=h) for h in helpers}
    asking = [_Asked(site, chapters=False) for site in _about_sites(source, title, set(answers))]
    if asking:
        _ask_all(net, asking, title, [], team="", say=say, deadline=ASK_DEADLINE)
    answers.update({a.site.name: a for a in asking})
    got = _about_decision(source, title, answers)[1]
    if got is not None:
        say(f"Описание — с {got[0].label}")
    return got


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
                "ReadManga, Desu, com-x.life, Manga One Love, MangaDex, HentaiLib, AllHentai, Хентай-тян или Nude-Moon")


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
    found, how = None, "по названию из ссылки"
    if named and named != words:                      # (v5.44.112) the name the tile said — the sites know it best
        say(f"{reason}. Ищу этот тайтл по названию «{name}» на {names}")
        found = find_by_words(net, named, sites, say=say)
        how = f"по названию «{name}»"
    if found is None and words:
        say(f"{reason}. Ищу этот тайтл по названию из ссылки («{said}») на {names}")
        found = find_by_words(net, words, sites, say=say)
        how = "по названию из ссылки"
    if found is None:
        asked = " и ".join(f"«{x}»" for x in dict.fromkeys(x for x in (str(name or "").strip(), said) if x))
        raise DownloadError(f"{reason}. По названию ({asked}) тайтл не нашёлся ни на одном из сайтов, "
                            f"которые программа читает ({names}). Если он там есть — вставьте ссылку оттуда.")
    other, title, _share = found
    say(f"{other.label}: нашёлся «{title.title}» — описание и главы оттуда и с других сайтов")
    return other, title, (f"{reason}. Поэтому тайтл найден на {other.label} {how} — описание оттуда, "
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


def _chapter(net: S.Net, take: Take, folder: Path, k: int, halt, night: bool = False) -> str:
    """One chapter into ``folder``: its pictures into ``.lb/partial/<name>`` (another site's — ``<name> (<site>)``: two
    sites' pictures never mix), then the whole folder moved in.  The chapter's folder (its name in the title's
    folder), or "" when ``halt()`` stopped it half-way («Остановить», (v5.44.74) «Пауза» — its pictures there stay).
    (v5.44.119) ``night`` — the nightly round brought it: its ``chapter.json`` says so (the title's page: «новая — пришла
    ночной проверкой»)."""
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
            "source": {"site": title.site, "id": ref.id, "url": ref.url}, "downloaded_at": int(_now()),
            **({"night": True} if night else {})}
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
    if not has_cover:
        _first_cover(found, folder)


def cover_ids(found: Found) -> list:
    """(v5.44.123) The names a title is known by on the sites it was found on («remanga:<dir>», «mangalib:<slug>»…):
    the owner's choice of its cover is kept under them (``core/title_covers``)."""
    return list(dict.fromkeys([f"{found.title.site}:{found.title.key}"]
                              + [f"{c['site']}:{c['key']}" for c in found.covers if c.get("key")]))


def _first_cover(found: Found, folder: Path) -> None:
    """The cover of a folder that has none: (v5.44.123) the one the owner chose while the title had no folder (the menu
    of covers), else the one the title was found with; what it is — noted (the menu knows the folder's is that site's)."""
    from core import title_covers as TC
    picture, came = found.cover, dict(found.cover_from)
    choice = TC.chosen(cover_ids(found))
    if choice is not None and choice.get("how") in ("site", "own") and choice.get("md5") != TC.md5_of(picture or b""):
        got = TC.picture_of(choice)
        if got is not None and _picture_type(got[0], got[1]):
            picture = got[0]
            came = {k: choice.get(k) for k in ("how", "site", "url", "file") if choice.get(k)}
    if not picture:
        return
    ext = _picture_type(picture, found.cover_type) or ".jpg"
    (folder / f"cover{ext}").write_bytes(picture)
    if came.get("how") in ("site", "own"):
        label = getattr(S.by_name(str(came.get("site") or "")), "label", "")
        TC.remember(cover_ids(found), **came, label=label, md5=TC.md5_of(picture), folder=str(folder),
                    name=found.title.title)


ENDED: list = []      # (v5.44.120) fn(found, result) at the end of every download (``core/download_history``: «Готово»)


def _looked(found: Found, home: S.Source, use: bool, silent: list) -> str:
    """(v5.44.146) Where a title's chapters were looked for besides its own site — for the end of a download some of
    whose chapters did not come: the sites that have the title (how many of their chapters are free), the ones that
    did not answer (``silent`` — their labels), the ones that have not the title.  "" — nothing to say (a title not
    in Russian: the other sites are not asked of it)."""
    if not use:
        return "Другие сайты не спрашивались — галочка «брать с других сайтов» снята"
    if (found.title.language or "ru") != "ru":
        return ""
    parts = []
    for helper in found.helpers:
        free = sum(1 for c in helper.chapters if c.free)
        parts.append(f"{helper.source.label} — бесплатных глав {free} из {len(helper.chapters)}")
    if silent:
        parts.append(f"{', '.join(silent)} — не ответил" + ("" if len(silent) == 1 else "и"))
    have = {h.source.name for h in found.helpers}
    searched = set(found.helper_record.get("helpers_sites") or [])
    absent = [site.label for site in S.readable() if site.name != home.name and site.name in searched
              and site.name not in have and site.label not in silent]
    if absent:
        parts.append(f"на {', '.join(absent)} этого тайтла нет")
    return "Другие сайты: " + "; ".join(parts) if parts else ""


def download(found: Found, *, chapters: list | None = None, follow: bool = True, helpers: "bool | None" = None,
             net: "S.Net | None" = None, say=None, stop_check=None, start_paused: bool = False,
             night: bool = False, retry: bool = False, hold=None) -> dict:
    """The chapters of ``found`` that the folder has not and some site has free (or ``chapters`` — their ids: the ones
    the page ticked) into the title's folder — (v5.44.62) from the other sites too (``helpers``; the title's own choice
    unless said); the paid ones nobody has free are kept in ``.lb/source.json`` with their dates; (v5.44.74) the new
    ones left out of ``chapters`` — as ``skipped`` (the nightly round leaves them); (v5.44.120) ``retry`` — «Повторить
    не скачавшееся» of «Загрузки → Готово»: ``chapters`` are the ones that did not come, the others are not made
    ``skipped`` by it (a chapter that came out since would never be taken at night).  «Пауза», the chapters asked
    again, the meter — ``core/manhwa_run.py``; ``start_paused`` — begun on «Пауза» (one the program closed on).
    (v5.44.119) ``night`` — the nightly round's download: its chapters say so (``chapter.json`` «night») and
    ``source.json`` keeps when the round looked and what it brought (``night_at``, ``night_got`` — the title's page:
    «Последняя проверка — сегодня в 03:12: пришли главы 40 и 41»).  (v5.44.163) ``hold()`` — the round's too: the
    person is at the computer, the next chapter waits for him to leave (``manhwa_run.wait_away``).
    A result for the page: the chapters that did not come — ``failed_chapters`` [{label, why, id}] (not «failed»: a
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
    if retry:                                      # (v5.44.120) the skipped ones as they were, less the ones retried
        record["skipped"] = sorted(str(x) for x in stored.get("skipped") or [] if str(x) not in (chosen or set()))
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
    asked_more: dict = {"done": False, "silent": list(found.late)}
    team = found.branch.name if found.branch and not getattr(source, "languages", False) else ""

    def rescue(k, left, why=""):
        """(v5.44.146) The other sites asked once more, once a download (``manhwa_run.go``): chapter ``k`` has come
        from none of the sites that had it (``why`` — their words) — or (None) the chapters are done and some wait
        for a free site, «скачать все».  Asked — for a chapter: every other site that is not one of the title's sites
        already (the ones a search of before did not find it on too); for the chapters that wait: the sites that did
        not answer when the title was found, and the ones not asked at all (:func:`_helper_plan` by what the title
        keeps) — at once, :data:`RESCUE_DEADLINE` seconds at most.  -> ({place: the take with more sites}, [takes to
        add]) or None."""
        if asked_more["done"] or not use or stop_check():
            return None
        if k is None and (chosen is not None or not found.waiting(True)):
            return None                            # nothing waits for a site (or the page ticked its chapters itself)
        asked_more["done"] = True
        kept = dict(found.helper_record)
        if k is not None and _now() - float(kept.get("helpers_at") or 0) > TRUST_SEARCH:
            # a chapter no site known gave: every other site is asked, the ones searched before too — «тайтла там
            # нет» of days ago may be no longer so, and until 5.44.146 a site that had not answered its search was
            # kept as searched (for a week); a search of minutes ago (this title's «Найти») is taken as it is.
            # Chapters that only wait for a free site (``k`` None) — as the title keeps it: a title with paid
            # chapters would search every site every night
            kept.update(helpers_at=0, helpers_sites=[], helpers_rule=HELPERS_RULE)
        sites = _helper_plan(source, found.title, kept)
        if sites is None:
            return None
        have = {h.source.name: h for h in found.helpers}
        asking = [_Asked(site, entry, done=True, other=have[site.name].title, helper=have[site.name])
                  if site.name in have else _Asked(site, entry) for site, entry in sites.sites]
        fresh = [a for a in asking if not a.done]
        if not fresh:
            return None
        names = ", ".join(a.site.label for a in fresh)
        minutes = max(1, int(round(RESCUE_DEADLINE / 60)))
        if k is not None:
            say(f"{wanted[k].label}: не скачалась — {why or 'её не дал ни один сайт'}. Спрашиваю ещё раз остальные "
                f"сайты: {names} (жду до {minutes} мин)")
            R.item(k, state="retry", why=R.SEEKING, again_at=time.monotonic() + RESCUE_DEADLINE, seek=True)
        else:
            say(f"Глав, которых нет бесплатно ни на одном из ответивших сайтов: {len(found.waiting(True))}. Спрашиваю "
                f"ещё раз остальные сайты: {names} (жду до {minutes} мин)")
        _ask_all(net, fresh, found.title, found.chapters, team=team, say=say, deadline=RESCUE_DEADLINE)
        if stop_check():
            return None
        found.helpers, found.helper_record = _helpers_of(sites, asking)
        found.asked_helpers = True
        found.late = [a.site.label for a in fresh if a.late]
        record.update(found.helper_record)
        try:                                       # «Читать» keeps the title's chapters by their places in the list
            from core import manga_online
            manga_online.ONLINE.forget_title(found)
        except Exception as exc:  # noqa: BLE001 - the reader's own memory: the download goes on all the same
            _LOG.warning("the reader's kept chapters of %s not let go: %s", found.title.title, exc)
        asked_more["silent"] = [a.site.label for a in fresh if a.failed]
        now = found.takes(True)
        by_id = {t.id: t for t in now}
        by_place = {(t.ch.tome, t.ch.number): t for t in now if t.ch.number}
        swaps: dict = {}
        for place in ([k] if k is not None else []) + list(left):
            old = wanted[place]
            other = by_id.get(old.id) or by_place.get((old.ch.tome, old.ch.number))
            if other is None or other.source is None:
                continue
            before = {o.source.name for o in R.options_of(old) if o.source is not None}
            if {o.source.name for o in R.options_of(other) if o.source is not None} - before:
                swaps[place] = wanted[place] = other
        added: list = []
        if chosen is None:                         # «скачать все»: what waited for a free site and has one now
            ours = {t.id for t in wanted} | {t.id for t in locked}
            places = {(t.ch.tome, t.ch.number) for t in [*wanted, *locked] if t.ch.number}
            left_out = {str(x) for x in record.get("skipped") or []}
            added = [t for t in now if t.source is not None and t.id not in ours and t.id not in left_out
                     and not (t.ch.number and (t.ch.tome, t.ch.number) in places)
                     and not found.is_had(t.ch) and not needs_sign_in(t)]
            wanted.extend(added)
        if not swaps and not added:
            say("Остальные сайты этих глав не дали")
            return None
        gave = ", ".join(dict.fromkeys(o.source.label for t in [*swaps.values(), *added] for o in R.options_of(t)
                                       if o.source is not None and o.source.name not in have and o.helped))
        say(f"Нашлось на других сайтах ({gave}): глав — {len(swaps) + len(added)}; качаю оттуда")
        return swaps, added
    try:                                           # «Пауза»: the strips on their way come, the next ones wait
        run = R.go(wanted, lambda take, k, halt: _chapter(net, take, folder, k, halt, night=night), say=say,
                   stop_check=stop_check, rescue=rescue, hold=hold)
    finally:
        R.resume()
    got = [name for _k, name, _take in run["got"]]
    others = sum(1 for _k, _name, take in run["got"] if take.helped)
    if asked_more["done"]:                         # (v5.44.146) the sites asked meanwhile: what waits for a site now
        paid = found.waiting(use)
        record["waiting"] = [{"id": c.id, "label": c.label, "free_at": c.free_at} for c in paid]
    looked = ""
    if not run["stopped"] and any(x.get("why") != R.SKIPPED for x in run["failed"]):
        looked = _looked(found, source, use, asked_more["silent"])
    if looked:
        say(looked)
    record["checked_at"] = int(_now())
    if night:                                      # (v5.44.119) the round's look at this title and what it brought
        record["night_at"] = record["checked_at"]
        record["night_got"] = [take.label for _k, _name, take in run["got"]]
    write_source(folder, record)
    _clean_partial(folder)
    if record.get("archive"):                      # (v5.44.76) a title of the archive: «Из архива» shows it as it is now
        try:
            from core import manga_archive
            manga_archive.note_download(folder, found)
        except (OSError, ValueError) as exc:
            _LOG.warning("the archive's counts of %s not noted: %s", folder, exc)
    failed = run["failed"] + [{"label": take.label, "why": take.source.sign_in_words(), "id": take.id}
                              for take in locked]
    result = {"title": found.title.title, "folder": str(folder), "kind": found.kind, "got": len(got),
              "chapters": got, "failed_chapters": failed, "paid": len(paid), "stopped": run["stopped"],
              "helped": others, "looked": looked,
              "gave_up": run["gave_up"], "skipped": len(record.get("skipped") or [])}
    if hold is not None and run["stopped"] and R.gave_way():     # (v5.44.163) the round gave way to the person's
        result["gave_way"] = True                                # download: «Готово» says so, not «Остановлено»
    R.values(finished=True, gave_up=run["gave_up"])
    for hook in list(ENDED):                       # (v5.44.120) «Загрузки → Готово»: never in the way of the download
        try:
            hook(found, result)
        except Exception as exc:  # noqa: BLE001 - a record of the history not made: the download is done all the same
            _LOG.warning("the end of the download of %s not noted: %s: %s", found.title.title, type(exc).__name__, exc)
    return result


def needs_sign_in(take: "Take") -> bool:
    """(v5.44.96) A chapter only a site that gives its pages to a sign-in alone has, and the program has no sign-in
    there (HentaiLib without the owner's MangaLib): asking would bring nothing."""
    return all(o.source is not None and o.source.SIGNED_ONLY and not o.source.signed_in() for o in R.options_of(take))


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


def _closing() -> bool:
    from core.shutdown import is_shutting_down
    return is_shutting_down()


def watch_round(settings: dict, *, say=None, stop_check=None, net: "S.Net | None" = None, closing=None,
                here=None, give_way=None) -> dict:
    """Every followed title: its site asked for chapters, the new free ones (and the paid ones that became free)
    downloaded.  Kept as done in ``manhwa_watch.json`` — (v5.44.116) a stopped one too, unless ``closing()`` says the
    stop is the program's closing (then the next start goes on with it).
    (v5.44.163) ``here()`` — the person is at the computer (``background_duties``: a key or the mouse touched, a page
    of the program opened, a video playing in the window): the next title and the next chapter wait for him to leave
    (``manhwa_run.wait_away``; «Продолжить» of «Загрузки» — ``manhwa_run.go_on`` — lets this round go on at once).
    None — a round the person asked for himself: it waits for nobody.  ``give_way()`` — a download of the person's
    waits in the queue (``manhwa_queue.waiting``): a round that waits for him to leave ends at once («gave_way» in
    its result; not kept as checked — the duty begins it again), else his «Скачать» would wait until he has left."""
    say = say or (lambda _text: None)
    stop_check = stop_check or (lambda: False)
    closing = closing or _closing
    net = net or S.Net(stop_check)
    titles = [(f, d) for f, d in followed(settings) if d.get("follow", True)]
    if not titles:
        say("Следить не за чем: тайтлов, скачанных программой, нет")
    R.let_go()                                        # (v5.44.163) a «Продолжить» of the round before is not this one's
    R.yield_to(give_way)
    try:
        total, problems, stopped = _round(titles, settings, say, stop_check, net, here)
        gave = R.gave_way() and not stop_check()
    finally:
        R.let_go()
    if gave:                                          # it ends for the person's download — not «stopped», not checked
        say("Вы поставили скачивание — проверка новых глав уступает ему и продолжится, когда вы отойдёте от компьютера")
        if total:
            say(f"Новых глав скачано: {total}")
        return {"titles": len(titles), "got": total, "problems": problems, "stopped": False, "gave_way": True}
    # (v5.44.116) «Остановить» means «until the next night» (the owner's review): a round the person stopped is kept as
    # checked — else the duty began it again ten minutes later; one the program's closing cut short is not
    if not stopped or not closing():
        from core.file_safety import write_json_kept
        write_json_kept(_watch_file(), json.dumps({"checked_at": int(_now()), "got": total, "titles": len(titles),
                                                   **({"stopped": True} if stopped else {})}), dict)
    if total:
        say(f"Новых глав скачано: {total}")
    return {"titles": len(titles), "got": total, "problems": problems, "stopped": stopped}


def _round(titles: list, settings: dict, say, stop_check, net: S.Net, here) -> tuple:
    """The titles of a round one after another (:func:`watch_round`): (chapters that came, [a title: why its site
    did not answer], stopped)."""
    total, problems, stopped = 0, [], False
    for folder, data in titles:
        if stop_check() or not R.wait_away(here, say, stop_check):
            stopped = True
            break
        name = data.get("title") or folder.name
        try:
            found = look(data.get("url") or "", settings, branch=str(data.get("branch") or ""), net=net,
                         where=str(data.get("kind") or ""), cover=False, stored=data, say=say,
                         helpers=bool(data.get("helpers_on", True)), about=False, elsewhere=False,
                         deadline=QUIET_DEADLINE)
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
            data["night_at"], data["night_got"] = data["checked_at"], []     # (v5.44.119) looked at: nothing new
            data["waiting"] = [{"id": c.id, "label": c.label, "free_at": c.free_at} for c in found.waiting()]
            data["licensed"] = found.title.licensed
            data.update(found.helper_record)
            write_source(folder, data)
            continue
        say(f"{name}: новых глав — {len(new)}")
        result = download(found, chapters=[t.id for t in new] if skipped else None, follow=bool(data.get("follow", True)),
                          net=net, say=say, stop_check=stop_check, night=True, hold=here)
        total += result["got"]
        if result.get("stopped"):
            stopped = True
            break
    return total, problems, stopped
