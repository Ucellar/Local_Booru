"""(v5.44.111) The comics of the gallery and the manga of the parser's folder go to the manga folder.

The owner (7.11.2026), of a comic whose pages the parser found on e621 one by one (his snapshot: the pages scattered
among the other cards of the gallery): «они обязанны были войти в мангу или хотя бы в сборку но не вошли»; «так же не
одной манги не прибавилось с момента как я начал парсить (а там 100% была манга)»; «если 15+ каринок в сборке то в мангу
и попытка найти оригинал»; where — «в папку манги», «своя папка у комикса».  The plan he said «да» to: the pages of one
pool of e621 — one сборка in the pool's order (``core/card_sets.py``); a comic of 15 pages and more — its files to the
manga folder, its cards to the trash with the reason «перенесено в мангу» (they come back from there); the сборки the
person made — only by «В мангу» on their block; after the move the program tries to find out what the manga is.

What is a comic of the gallery (:func:`candidates`):

* a pool of e621 of the category «series» (``site_pools``; its cards — ``site_posts.pools``): its pages in the pool's
  order, its name the pool's;
* a сборка of the program's of the kinds «work» and «series» (not «parent» — the versions of one picture — nor one the
  person made) whose pictures carry a comic's tag (``card_sets._COMIC``) — :data:`COMIC_SHARE` of them at least;

with :data:`MIN_PAGES` live picture cards at least.  The program's gate holds here too: a comic ``gates.pages`` closes
by what the base says of its pages (:func:`_pages_said`: their tags, the sites' ratings) is never taken — it stays
where it is.

How (:func:`move`): the pages are copied — «<name>/001.jpg …» (a pool's by their place in the pool) — first into
``<manga folder>/.lb/copying/<key>`` (:func:`_staging`: the library does not see a half-made title; a copy cut short
goes on there), each copy read back and checked against its card's MD5; ``info.json`` — the name, the link (the pool on
e621), the cards' tags by kind; then the folder is renamed into place, and the cards go to the trash
(``library_lifecycle.move_to_trash``, reason :data:`REASON`): their files with them, «Вернуть» brings a card back (it is
not taken again: what was moved is kept by its cards), the manga's copy stays.

The manga of the parser's folder: (v5.44.125) ``core/parser_manga.py`` — only a manga a site of manga confirms
(ComicInfo.xml, a link in its files, a title of the same pages, SauceNAO → a site; never e621) is copied, by the
parser's run and by this duty's rounds (its
``background_round``), with this module's staging, checked copies, records «root:<id>» and :func:`_place`; a folder
nothing confirms is the parser's (a сборка of the gallery).  The program never moves, changes nor removes the parser's
files; the gallery's cards of the same bytes stay.  Not when the manga folder's disk would keep less than
:data:`KEEP_FREE`.  (Before: v5.44.111 — a folder of 15 numbered pages copied by itself; v5.44.115 — only by
«Проверить сейчас»; ``copy_title`` — gone with v5.44.125.)

(v5.44.125) The owner: «пулы в e621 бывают напичканы рандомными картинками так что я говорил что бы подтвержденная
манга была именно с манга сайтов» — a comic of the gallery (a pool of e621 too) goes to the manga folder by itself only
when a site of manga confirms it (:func:`_site_says` — ``parser_manga.confirm_pages``: its name searched on the sites of
manga, its first pages compared with the title's by pHash; SauceNAO after), and its copy is linked to that title (its
description, the chapters it misses — «Загрузки»); else it stays a сборка of the gallery.  What the sites said —
``comic_manga_checks.json`` in the settings folder (asked again after :data:`AGAIN_DAYS`).  «В мангу» of a сборка's
block (:func:`of_set`) — the person's word, as before.

What was moved — ``comic_manga.json`` in the settings folder ({key: {folder, title, at, pages, cards, done…}}): a
pool's pages that come later go into the same folder; a folder the person removed from the manga folder is not made
again.  (v5.44.116) A record left «done: False» (the rename into place cut short) is finished by the next round
(:func:`_finish_cut_short`), not copied a second time; «В мангу» takes the round's lock too (:func:`round_held`).
The manga is then looked up on the sites of manga by the background of «Из архива» (``manga_archive.entries``
takes these folders — :func:`archive_entries`; a copied title of the parser's folder is known by its copy): the name,
the description, the cover and the missing chapters, as it does the parser's folder's titles.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core import gates as G

_LOG = logging.getLogger("local_booru.card_sets")

MIN_PAGES = 15                  # pictures of a comic at least (the owner: «если 15+ каринок в сборке то в мангу»)
COMIC_SHARE = 0.5               # …of a сборка: so many of them with a comic's tag
REASON = "moved_to_manga"       # the trash's reason (``trash_tools.LABELS``)
KEEP_NAME = "comic_manga.json"
NAME_MAX = 120
TAGS_MAX = 60                   # general tags written into info.json (the common ones first)
_PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".jxl"}
STAGING = "copying"             # <manga folder>/.lb/copying/<key's hash>: a copy not finished yet
ROUND_SECONDS = 45              # a round of the background works so long at most (the duties share one thread)…
KEEP_FREE = 10 * 1024 ** 3      # the manga folder's disk keeps so much free after a copy
TASK = "comic_manga"            # (v5.44.113) «Проверить сейчас» — a task of the pages (``core/page_jobs``)
CHECKS_NAME = "comic_manga_checks.json"     # (v5.44.125) what the sites of manga said of a comic not moved yet
AGAIN_DAYS = 7                  # …«not one» asked again after so many days
HAND_WAIT = ROUND_SECONDS + 15  # (v5.44.116) «В мангу» waits so long for a round going, then says so
_LOCK = threading.Lock()
_ROUND = threading.Lock()       # (v5.44.113) one round at a time: the duty and «Проверить сейчас» never copy together;
#                                 (v5.44.116) «В мангу» too (:func:`round_held`)
_LOOKING = threading.Lock()     # …and one look at the parser's folder started by this module
_STATE: dict = {"at": 0, "moved": 0, "copied": 0, "no_room": "", "looked_at": 0}     # the last rounds, for the page


class ComicError(RuntimeError):
    """Said to the person as it is."""


class NoRoom(ComicError):
    """The manga folder's disk would keep less than :data:`KEEP_FREE`."""


@dataclass
class Comic:
    key: str                    # «pool:e621.net:15766» / «set:12»
    title: str
    cards: list                 # [(image id, place)] in reading order — place: the page's number from 1
    source_url: str = ""
    kind: str = ""              # «pool» / «set»
    more: bool = False          # pages of a comic moved before (its folder takes them)
    set_ids: list = field(default_factory=list)

    def public(self) -> dict:
        return {"key": self.key, "title": self.title, "pages": len(self.cards), "kind": self.kind,
                "source_url": self.source_url, "more": self.more}


# ── what was moved ─────────────────────────────────────────────────────────────────────────────────────────────────
def _keep_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / KEEP_NAME


def moved() -> dict:
    """{key: {"folder", "title", "at", "pages"}} of the comics moved to the manga folder."""
    from core.file_safety import read_json_kept
    data = read_json_kept(_keep_file(), dict, what="comic manga")
    return data if isinstance(data, dict) else {}


def _remember(key: str, record: dict) -> None:
    from core.file_safety import write_json_kept
    with _LOCK:
        data = moved()
        old = data.get(key) if isinstance(data.get(key), dict) else {}
        data[key] = {**old, **record}
        write_json_kept(_keep_file(), json.dumps(data, ensure_ascii=False, indent=1), dict)


# ── what is a comic ────────────────────────────────────────────────────────────────────────────────────────────────
def enabled(settings: dict) -> bool:
    """«Настройки → Галерея → Комиксы из галереи — в папку манги» (on by default: the owner asked for it)."""
    return bool((settings or {}).get("comic_manga_auto", True))


def _pictures_alive(con) -> set:
    from core.card_sets import VIDEO_EXTS
    ends = tuple(VIDEO_EXTS)
    return {int(r[0]) for r in con.execute("SELECT id, path, is_video FROM images WHERE deleted=0")
            if not r[2] and not str(r[1] or "").lower().endswith(ends)}


def _manual_cards(con) -> set:
    return {int(r[0]) for r in con.execute("SELECT m.image_id FROM card_set_members m JOIN card_sets s "
                                           "ON s.set_id=m.set_id WHERE s.kind='manual'")}


def pool_name(name: str) -> str:
    """e621's name of a pool («Silver_Soul_Origins,_The_Twins») as a title («Silver Soul Origins, The Twins»)."""
    return re.sub(r"\s+", " ", str(name or "").replace("_", " ")).strip()


def _pools(con, alive: set, manual: set, done: dict) -> list:
    out: list = []
    rows = con.execute("SELECT site, pool_id, name, post_ids FROM site_pools WHERE state='ok' AND category='series'")
    pools = {}
    for site, pool_id, name, post_ids in rows:
        try:
            order = [int(x) for x in json.loads(post_ids or "[]") if str(x).isdigit()]
        except ValueError:
            order = []
        pools[(str(site), int(pool_id))] = (str(name or ""), {pid: k for k, pid in enumerate(order)})
    if not pools:
        return out
    cards: dict = {}                                    # (site, pool) -> {post id: image id}
    for image_id, site, post_id, text in con.execute(
            "SELECT image_id, site, post_id, pools FROM site_posts WHERE state='ok' AND pools IS NOT NULL "
            "AND pools != ''"):
        if int(image_id) not in alive:
            continue
        for part in str(text or "").split(","):
            if part.isdigit() and (str(site), int(part)) in pools:
                cards.setdefault((str(site), int(part)), {}).setdefault(int(post_id), int(image_id))
    from core.card_sets import pool_key
    for (site, pool_id), posts in cards.items():
        name, places = pools[(site, pool_id)]
        key = pool_key(site, pool_id)
        ordered = sorted(posts.items(), key=lambda kv: (places.get(kv[0], 10 ** 6), kv[0]))
        chosen, seen = [], set()
        for post_id, image_id in ordered:
            if image_id in seen:
                continue
            seen.add(image_id)
            chosen.append((image_id, places.get(post_id, len(places) + len(chosen)) + 1))
        if any(image_id in manual for image_id, _place in chosen):
            continue                                    # the person's own сборка: by the person only
        record = done.get(key) if isinstance(done.get(key), dict) else None
        more = record is not None
        if more:
            if record.get("done", True) and not Path(str(record.get("folder") or "")).is_dir():
                continue                                # the person took the manga out of the manga folder
            before = {int(i) for i in record.get("cards") or [] if str(i).isdigit()}
            chosen = [(i, place) for i, place in chosen if i not in before]     # «Вернуть» is not undone
        if not chosen or (not more and len(chosen) < MIN_PAGES):
            continue
        host = site if "." in site else "e621.net"
        out.append(Comic(key=key, title=pool_name(name) or f"Пул {pool_id}", cards=chosen, kind="pool", more=more,
                         source_url=f"https://{host}/pools/{pool_id}"))
    return out


def _comic_marks(con, ids: list) -> set:
    from core.card_sets import _COMIC
    names = sorted(_COMIC)
    out: set = set()
    for start in range(0, len(ids), 800):
        part = ids[start:start + 800]
        marks, words = ",".join("?" * len(part)), ",".join("?" * len(names))
        out |= {int(r[0]) for r in con.execute(
            f"SELECT DISTINCT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id "
            f"WHERE it.image_id IN ({marks}) AND LOWER(t.normalized_name) IN ({words})", part + names)}
    return out


def _cards_left(record) -> bool:
    """(v5.44.116) A comic copied whose cards never went to the trash — its rename into place cut short (finished by
    :func:`_finish_cut_short`) or the trash refused —, its folder there: taken again, its cards go (the copies in its
    folder are kept, not made anew)."""
    return isinstance(record, dict) and not record.get("cards") and record.get("done", True) \
        and Path(str(record.get("folder") or "")).is_dir()


def _sets(con, alive: set, taken: set, done: dict, *, set_id: int = 0, any_kind: bool = False) -> list:
    out: list = []
    where = "WHERE set_id=?" if set_id else "WHERE kind IN ('work', 'series')"
    for sid, kind, title in con.execute(f"SELECT set_id, kind, title FROM card_sets {where}",
                                        (int(set_id),) if set_id else ()).fetchall():
        key = f"set:{int(sid)}"
        if key in done and not set_id and not _cards_left(done[key]):
            continue
        if not any_kind and str(kind) not in ("work", "series"):
            continue
        ids = [int(r[0]) for r in con.execute("SELECT image_id FROM card_set_members WHERE set_id=? "
                                              "ORDER BY position, image_id", (int(sid),))]
        ids = [i for i in ids if i in alive and i not in taken]
        if len(ids) < (1 if set_id else MIN_PAGES):
            continue
        if not set_id:
            comic = _comic_marks(con, ids)
            if len(comic) < COMIC_SHARE * len(ids):
                continue
        out.append(Comic(key=key, title=str(title or "").strip() or _set_title(con, ids, int(sid)),
                         cards=[(i, n) for n, i in enumerate(ids, 1)], kind="set", set_ids=[int(sid)]))
    return out


def _tags_of(con, ids: list) -> dict:
    """{image id: [(name, category)]}."""
    out: dict = {}
    for start in range(0, len(ids), 800):
        part = ids[start:start + 800]
        marks = ",".join("?" * len(part))
        for image_id, name, category in con.execute(
                f"SELECT it.image_id, t.name, t.category FROM image_tags it JOIN tags t ON t.id=it.tag_id "
                f"WHERE it.image_id IN ({marks})", part):
            out.setdefault(int(image_id), []).append((str(name or ""), str(category or "general")))
    return out


def _set_title(con, ids: list, set_id: int) -> str:
    """A name of a сборка the person did not name: its artist and its copyright, as most of its pages have them."""
    tags = _tags_of(con, ids)
    count: dict = {}
    for pairs in tags.values():
        for name, category in set(pairs):
            if category in ("artist", "copyright"):
                count[(category, name)] = count.get((category, name), 0) + 1
    best = {}
    for (category, name), n in sorted(count.items(), key=lambda kv: -kv[1]):
        best.setdefault(category, name)
    words = [best[c].replace("_", " ") for c in ("artist", "copyright") if c in best]
    return " — ".join(words) if words else f"Сборка {set_id}"


def _pages_said(con, comic: Comic) -> list:
    """What the base says of a comic's pages, for the program's gate (``gates.pages``): (the tags' names, the sites'
    ratings) of each picture — not rated by a site: {""}."""
    ids = [i for i, _place in comic.cards]
    tags = _tags_of(con, ids)
    ratings: dict = {}
    for start in range(0, len(ids), 800):
        part = ids[start:start + 800]
        marks = ",".join("?" * len(part))
        for image_id, rating in con.execute(f"SELECT image_id, rating FROM site_posts WHERE state='ok' "
                                            f"AND image_id IN ({marks})", part):
            ratings.setdefault(int(image_id), set()).add(str(rating or ""))
    return [([name for name, _category in tags.get(image_id, [])], ratings.get(image_id) or {""}) for image_id in ids]


def candidates(settings: dict) -> list:
    """The comics of the gallery to move now (:class:`Comic`): the pools first, then the сборки the pools did not take."""
    from core.database.connection import db
    done = moved()
    with db(settings, readonly=True) as con:
        alive = _pictures_alive(con)
        manual = _manual_cards(con)
        out = _pools(con, alive, manual, done)
        taken = {i for c in out for i, _place in c.cards}
        out += _sets(con, alive, taken, done)
        return [c for c in out if not G.pages(_pages_said(con, c))]


def of_set(settings: dict, set_id: int) -> Comic:
    """«В мангу» on a сборка's block: the сборка as a comic (any kind, the person's too; a pool's pages — the pool's
    order and name when the сборка is one pool)."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        alive = _pictures_alive(con)
        found = _sets(con, alive, set(), {}, set_id=int(set_id), any_kind=True)
        if not found:
            raise ComicError("В сборке нет картинок, которые можно перенести")
        comic = found[0]
        if len(comic.cards) < 2:
            raise ComicError("В сборке одна картинка — это не манга")
        ids = {i for i, _place in comic.cards}
        for pool in _pools(con, alive, set(), {}):    # one pool: its order, its name, its link
            if {i for i, _place in pool.cards} == ids:
                pool.set_ids = [int(set_id)]
                comic = pool
                break
        why = G.pages(_pages_said(con, comic))
        if why:
            raise ComicError(why)                     # a gate closed it
    return comic


# ── the move ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _safe(text: str) -> str:
    from core.manhwa_download import safe_name
    return safe_name(text, NAME_MAX) or "Комикс"


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_checked(source: Path, target: Path, md5: str = "") -> str:
    """``source`` copied to ``target`` through «<name>.part», the copy read back and checked (and against ``md5`` —
    the card's — when given): the MD5 of its bytes.  :class:`OSError` — not copied (nothing left behind)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest = hashlib.md5()
    try:
        with open(source, "rb") as src, open(part, "wb") as out:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(chunk)
                out.write(chunk)
        said = digest.hexdigest()
        if md5 and said != md5.lower():
            raise OSError("файл карточки не совпал с её MD5")
        if _md5(part) != said:
            raise OSError("копия не совпала с файлом")
        try:
            shutil.copystat(source, part)
        except OSError:
            pass                                        # its time is not worth a page
        os.replace(part, target)
    except BaseException:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return said


def _kept_copy(source: Path, target: Path) -> str:
    """The MD5 of ``target`` when it is already the copy of ``source`` (a copy cut short goes on): "" — it is not."""
    try:
        if target.is_file() and target.stat().st_size == source.stat().st_size:
            return _md5(target)
    except OSError:
        pass
    return ""


def _staging(root: Path, key: str) -> Path:
    return root / ".lb" / STAGING / hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def _unique(root: Path, name: str) -> Path:
    base = _safe(name)
    folder, n = root / base, 2
    while folder.exists():
        folder, n = root / f"{base} ({n})", n + 1
    return folder


def _info(con, comic: Comic, ids: list) -> dict:
    """``info.json`` of the manga: the name, the link, the cards' tags by kind (general — those of a third of the pages
    and more, the common ones first)."""
    tags = _tags_of(con, ids)
    count: dict = {}
    for pairs in tags.values():
        for name, category in set(pairs):
            count[(category, name)] = count.get((category, name), 0) + 1
    kinds = {"artist": "artist", "copyright": "parody", "character": "character", "species": "general",
             "general": "general", "meta": "category"}
    out: list = []
    general = 0
    for (category, name), n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0][1])):
        kind = kinds.get(category)
        if not kind or not name:
            continue
        if kind == "general":
            if general >= TAGS_MAX or n < max(1, len(ids) // 3):
                continue
            general += 1
        out.append({"type": kind, "name": name.replace("_", " ")})
    data = {"title": comic.title, "tags": out, "from_gallery": {"cards": len(ids), "key": comic.key,
                                                               "at": int(time.time())}}
    if comic.source_url:
        data["source_url"] = comic.source_url
    return data


def _write_info(folder: Path, info: dict) -> None:
    """``info.json``: a folder that has one keeps what it says (pages that came later, a manga the sites named)."""
    from core.file_safety import write_json_kept
    path = folder / "info.json"
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        if isinstance(old, dict):
            info = {**info, **old}
    write_json_kept(path, json.dumps(info, ensure_ascii=False, indent=1), dict)


def _root_of(settings: dict) -> Path:
    from core import manga_library as M
    text = M.manga_root(settings)
    if not text:
        raise ComicError("Не выбрана папка манги («Настройки → Хранение и копии → Папка манги»)")
    root = Path(text)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _record(key: str) -> dict:
    record = moved().get(key)
    return record if isinstance(record, dict) else {}


def old_gone(key: str, record, sure: "dict | None" = None) -> bool:
    """(v5.44.127) A copy the rules before 5.44.125 made — no site of manga confirmed it (a pool of e621 by itself, a
    сборка, a folder of the parser by its names: ``kind`` «root», no ``confirmed``) — whose folder is not in the manga
    folder any more.  The owner took the old copies out to parse everything again (7.11.2026: «Я уже удалил все»; «если
    оно как то записалось что это манга и оно не будет парсить это пиздец я удалял как раз для перепаршивания»): such a
    record is forgotten, not «the person took it out — never again».  A copy the rule of 5.44.125 made (a site of manga
    confirmed it: ``confirmed`` / ``kind`` «parser», or ``comic_manga_checks.json`` says «manga») taken out — never
    again, as before.  A copy cut short (``done`` False, its folder named) — :func:`_finish_cut_short` finishes it."""
    if not isinstance(record, dict):
        return False
    folder = str(record.get("folder") or "")
    if folder and Path(folder).is_dir():
        return False
    if folder and not record.get("done", True):
        return False
    if record.get("confirmed") or str(record.get("kind") or "") == "parser":
        return False
    sure = checks() if sure is None else sure
    return (sure.get(key) or {}).get("state") != "manga"


def forget(key: str) -> None:
    """(v5.44.127) A record of ``comic_manga.json`` dropped (:func:`old_gone`)."""
    from core.file_safety import write_json_kept
    with _LOCK:
        data = moved()
        if data.pop(key, None) is not None:
            write_json_kept(_keep_file(), json.dumps(data, ensure_ascii=False, indent=1), dict)


def forget_old_gone() -> int:
    """(v5.44.127) The records of the old copies taken out (:func:`old_gone`) dropped — how many: their comics and the
    folders of the parser are as new (a site of manga confirms them — copied again)."""
    from core.file_safety import write_json_kept
    sure = checks()
    with _LOCK:
        data = moved()
        gone = [key for key, record in data.items() if old_gone(key, record, sure)]
        for key in gone:
            data.pop(key, None)
        if gone:
            write_json_kept(_keep_file(), json.dumps(data, ensure_ascii=False, indent=1), dict)
    if gone:
        _LOG.info("comic manga: %d records of old copies taken out of the manga folder forgotten", len(gone))
    return len(gone)


def _place(root: Path, key: str, work: Path, name: str, record: dict) -> Path:
    """The finished copy renamed into place (remembered first: a copy whose rename was cut short is not made twice —
    :func:`_finish_cut_short`)."""
    final = _unique(root, name)
    _remember(key, {**record, "folder": str(final), "done": False})
    os.replace(work, final)
    _remember(key, {"done": True})
    try:
        (root / ".lb" / STAGING).rmdir()                # empty now: nothing is being copied
    except OSError:
        pass
    return final


def _finish_cut_short(root: Path) -> list:
    """(v5.44.116) A record left «done: False» — the program closed (or the rename failed) between the note of the
    copy's place and the note that it is there (the review of 7.11.2026: such a title was copied again as «Имя (2)»,
    such a сборка never finished): its folder there — the record is done; else its finished copy still in
    ``.lb/copying`` — renamed into place now.  [(key, the folder's name)] finished (a comic's cards then go to the
    trash as usual: :func:`_pools`, :func:`_cards_left`).  Under ``_ROUND``."""
    out: list = []
    for key, record in moved().items():
        if not isinstance(record, dict) or record.get("done", True) or not record.get("folder"):
            continue
        final = Path(str(record["folder"]))
        work = _staging(root, key)
        try:
            if final.is_dir():
                _remember(key, {"done": True})
            elif work.is_dir():
                final = _place(root, key, work, final.name, record)
            else:
                continue                                # neither: the next round copies it anew (its key is not done)
        except OSError as exc:
            _LOG.warning("comic %s: its copy not put into place: %s", key, exc)
            continue
        _LOG.info("comic %s: the copy cut short finished → %s", key, final)
        out.append((key, final.name))
    return out


@contextlib.contextmanager
def round_held(wait: "float | None" = None):
    """(v5.44.116) «В мангу» on a сборка's block (``webui/pages/post_sets.to_manga``) under the round's own lock: the
    background and «Проверить сейчас» never copy at the same time with it.  A round going is waited for ``wait``
    seconds (:data:`HAND_WAIT`), then :class:`ComicError` in words."""
    if not _ROUND.acquire(timeout=max(0.0, float(HAND_WAIT if wait is None else wait))):
        raise ComicError("Программа сейчас сама переносит комиксы в папку манги — нажмите «В мангу» ещё раз через "
                         "минуту")
    try:
        yield
    finally:
        _ROUND.release()


def move(settings: dict, comic: Comic, *, say=None) -> dict:
    """The comic's pages copied into the manga folder (each checked), ``info.json`` written, its cards to the trash —
    {"folder", "name", "pages", "trashed", "skipped"}.  A page whose copy does not match its card stays in the gallery."""
    from core import manga_library as M
    from core.database.connection import db
    from core.library_lifecycle import move_to_trash
    say = say or (lambda _text: None)
    root = _root_of(settings)
    record = _record(comic.key)
    kept = Path(str(record["folder"])) if record.get("folder") and record.get("done", True) else None
    if kept is not None and not kept.is_dir():
        kept = None
    work = kept or _staging(root, comic.key)
    ids = [i for i, _place in comic.cards]
    with db(settings, readonly=True) as con:
        rows = {}
        for start in range(0, len(ids), 800):
            part = ids[start:start + 800]
            marks = ",".join("?" * len(part))
            for row in con.execute(f"SELECT * FROM images WHERE id IN ({marks}) AND deleted=0", part):
                rows[int(row["id"])] = dict(row)
        info = _info(con, comic, ids)
    work.mkdir(parents=True, exist_ok=True)
    width = max(3, len(str(max((p for _i, p in comic.cards), default=1))))
    copied, skipped = [], 0
    for image_id, place in comic.cards:
        row = rows.get(image_id)
        source = Path(str((row or {}).get("path") or ""))
        ext = source.suffix.lower()
        if row is None or ext not in _PICTURES or not source.is_file():
            skipped += 1
            continue
        expected = str(row.get("hash_md5") or "").lower()
        target = work / f"{place:0{width}d}{ext}"
        if target.exists() and _kept_copy(source, target) != expected:
            target = work / f"{place:0{width}d}-{image_id}{ext}"      # another picture at this place
        if not (expected and _kept_copy(source, target) == expected):
            try:
                _copy_checked(source, target, expected)
            except OSError as exc:
                _LOG.warning("comic %s: page %s (card %s) not copied: %s", comic.key, place, image_id, exc)
                skipped += 1
                continue
        copied.append(row)
    if not copied:
        if kept is None:
            shutil.rmtree(work, ignore_errors=True)    # the program's own unfinished copy, nothing in it
        raise ComicError("Ни одна страница не скопировалась — файлы карточек не читаются")
    _write_info(work, info)
    before = [int(i) for i in record.get("cards") or [] if str(i).isdigit()]
    fresh = {"title": comic.title, "at": int(time.time()), "kind": comic.kind, "source_url": comic.source_url,
             "pages": max(int(record.get("pages") or 0), len(before) + len(copied))}
    if kept is None:
        folder = _place(root, comic.key, work, comic.title, {**record, **fresh})
    else:
        folder = kept
        _remember(comic.key, fresh)
    result = move_to_trash(settings, copied, reason=REASON, tag_or_source=folder.name[:200])
    trashed = int(result.get("trashed_records") or 0)
    if result.get("error"):                         # (v5.44.115) the trash refused (no copy of the base): the cards
        _LOG.warning("comic %s: cards not trashed: %s", comic.key, result["error"])     # stay, the next round tries
    else:                                           # again — only the cards it took are «moved» («Вернуть» stays)
        _remember(comic.key, {"cards": sorted(set(before) | {int(r["id"]) for r in copied})})
    M.LIBRARY.invalidate()
    say(f"«{comic.title}»: {len(copied)} стр. в папке манги «{folder.name}», в корзину — {trashed}"
        + (f", не скопировано — {skipped}" if skipped else ""))
    _LOG.info("comic %s → %s: %d pages copied, %d cards trashed, %d skipped", comic.key, folder, len(copied), trashed,
              skipped)
    return {"folder": str(folder), "name": folder.name, "pages": len(copied), "trashed": trashed, "skipped": skipped,
            "page": page_of(settings, folder), "error": str(result.get("error") or "")}


def page_of(settings: dict, folder) -> str:
    """The address of the manga's page in «Манга» (its id — of its place in the manga folder)."""
    from core import manga_library as M
    root = M.manga_root(settings)
    if not root:
        return "/manga"
    return f"/manga/g/{M._id_for(Path(root), Path(str(folder)))}"


# ── the background ─────────────────────────────────────────────────────────────────────────────────────────────────
def _look_if_due(settings: dict) -> bool:
    """(v5.44.113) The parser's folder looked at in a thread of its own when it is due (``manga_detect.STORE.due``: the
    rules changed — this version —, six hours went by, a manga seen since) — not only by the background of «Из архива»,
    which «Узнавать само» switches off: True — a look goes (its titles are copied by the next rounds)."""
    from core import manga_detect as MD
    root = str((settings or {}).get("root") or "").strip()
    if not root or not Path(root).is_dir() or not MD.STORE.due(root):
        return False
    if not _LOOKING.acquire(blocking=False):
        return True

    def look() -> None:
        try:
            MD.look(dict(settings))
            _STATE["looked_at"] = int(time.time())
        except Exception as exc:  # noqa: BLE001 - the next round looks again
            _LOG.warning("the parser's folder not looked at: %s: %s", type(exc).__name__, exc)
        finally:
            _LOOKING.release()
    threading.Thread(target=look, name="comic-manga-look", daemon=True).start()
    return True


def background_round(settings: dict, *, stop_check=None, wait: bool = False, parser: bool = False,
                     say=None) -> dict:
    """The comics of the gallery and the manga of the parser's folder to the manga folder (the background duty
    «comic_manga», :data:`ROUND_SECONDS` at most): {"moved", "copied", "more", "no_room",
    "busy"}; (v5.44.113) one round at a time (``wait`` — for the one going, else "busy"); (v5.44.125) ``say`` — the
    lines of the parser's manga for a journal («Проверить сейчас»)."""
    if not _ROUND.acquire(blocking=wait):
        return {"moved": [], "copied": [], "more": False, "no_room": "", "wait": 0.0, "busy": True}
    try:
        out = _round(settings, stop_check=stop_check, parser=parser, say=say)
    finally:
        _ROUND.release()
    _STATE.update({"at": int(time.time()), "moved": _STATE["moved"] + len(out["moved"]),
                   "copied": _STATE["copied"] + len(out["copied"]), "no_room": out["no_room"]})
    return out


def _round(settings: dict, *, stop_check=None, parser: bool = False, say=None) -> dict:
    """(v5.44.115) The comics of the gallery; the manga of the parser's folder only when ``parser`` — «Проверить
    сейчас», never the background: a folder taken for a manga may be an artist's numbered set (the review of
    7.11.2026), so the person sees the list in the journal; its cards in the gallery are never touched.  (v5.44.125)
    The manga of the parser's folder — in the background too, now that only a manga a site of manga confirms is copied
    (``core/parser_manga.py``: ComicInfo.xml, a link, a title of a site whose pages are the folder's — never e621, never
    a folder of numbers alone); a comic of the gallery — only when a site of manga confirms it (:func:`_site_says`)."""
    stop_check = stop_check or (lambda: False)
    from core import manga_library as M
    out = {"moved": [], "copied": [], "more": False, "no_room": "", "wait": 0.0}
    if not enabled(settings) or not M.manga_root(settings):
        return out
    finished = _finish_cut_short(Path(M.manga_root(settings)))     # (v5.44.116) a rename cut short — finished first
    if finished:
        M.LIBRARY.invalidate()
        out["copied"] += [name for key, name in finished if key.startswith("root:")]   # a comic's — with its cards
    forget_old_gone()                                   # (v5.44.127) the old copies the owner took out: as new
    _look_if_due(settings)                              # the parser's folder kept known (the parser skips its manga)
    deadline = time.monotonic() + ROUND_SECONDS
    for comic in candidates(settings):
        if stop_check() or time.monotonic() > deadline:
            out["more"] = True
            return out
        said = _site_says(settings, comic, stop_check=stop_check)     # (v5.44.125) a site of manga confirms it
        if said.get("state") != "manga":
            continue                                    # a сборка of the gallery it stays
        try:
            got = move(settings, comic)
        except (ComicError, OSError) as exc:
            _LOG.info("comic %s not moved: %s", comic.key, exc)
            continue
        out["moved"].append(got["name"])
        if said.get("url") and not comic.more:          # its copy linked to the title that confirmed it
            from core import parser_manga
            parser_manga.link_folder(settings, f"comic:{comic.key}", comic.title, Path(got["folder"]), said["url"],
                                     how=str(said.get("how") or ""), by=str(said.get("by") or ""),
                                     stop_check=stop_check)
    from core import parser_loose                       # (v5.44.164) the pages of manga among the loose pictures:
    left = deadline - time.monotonic()                  # a short part of the round (it works only when it has what)
    if left > 1 and not stop_check():
        try:
            got = parser_loose.background_round(settings, stop_check=stop_check, say=say,
                                                seconds=min(float(parser_loose.ROUND_SECONDS), left))
        except Exception as exc:  # noqa: BLE001 - its trouble does not stop the folders' round
            _LOG.warning("the loose pictures' round failed: %s: %s", type(exc).__name__, exc, exc_info=True)
            got = {}
        out["more"] = out["more"] or bool(got.get("more"))
        out["wait"] = float(got.get("wait") or 0)
    if _LOOKING.locked():
        return out                                      # (the parser's folder is being looked at: next time)
    from core import parser_manga                       # (v5.44.125) only what is confirmed a manga is copied
    left = deadline - time.monotonic()
    if left > 1 and not stop_check():
        got = parser_manga.background_round(settings, stop_check=stop_check, seconds=left, say=say)
        out["copied"] += list(got.get("copied") or [])
        out["more"] = out["more"] or bool(got.get("more"))
        out["wait"] = max(out["wait"], float(got.get("wait") or 0))
        if got.get("no_room"):
            out["no_room"] = str(got["no_room"])
    return out


# ── (v5.44.125) what the sites of manga say of a comic of the gallery ─────────────────────────────────────────────
def _checks_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / CHECKS_NAME


def checks() -> dict:
    """{a comic's key: {"state": "manga" | "not" | "wait", "url", "title", "by", "how", "at", "later_at"}}."""
    from core.file_safety import read_json_kept
    data = read_json_kept(_checks_file(), dict, what="comic manga checks")
    return data if isinstance(data, dict) else {}


def _check_put(key: str, record: dict) -> None:
    from core.file_safety import write_json_kept
    with _LOCK:
        data = checks()
        data[key] = record
        write_json_kept(_checks_file(), json.dumps(data, ensure_ascii=False, indent=1), dict)


def _card_paths(settings: dict, comic: Comic, count: int) -> list:
    """The files of the first ``count`` live cards of a comic, in its order."""
    from core.database.connection import db
    out = []
    with db(settings, readonly=True) as con:
        for image_id, _place in sorted(comic.cards, key=lambda c: c[1]):
            row = con.execute("SELECT path FROM images WHERE id=? AND deleted=0", (int(image_id),)).fetchone()
            if row and row[0] and Path(str(row[0])).is_file():
                out.append(str(row[0]))
            if len(out) >= count:
                break
    return out


def _site_says(settings: dict, comic: Comic, *, stop_check=None) -> dict:
    """(v5.44.125) Whether a site of manga confirms a comic of the gallery (``parser_manga.confirm_pages``: its name
    searched on the sites, its first pages compared with the title's; SauceNAO after) — {"state": "manga" | "not" |
    "wait", "url", "title", "by", "how"}, kept: «not» asked again after :data:`AGAIN_DAYS`, «wait» after its time.  A
    comic moved before (its later pages) — a manga already."""
    if comic.more:
        return {"state": "manga"}
    kept = checks().get(comic.key) or {}
    now = time.time()
    if kept.get("state") == "manga" or (kept.get("state") == "not" and now - float(kept.get("at") or 0) <
                                        AGAIN_DAYS * 86400) or (kept.get("state") == "wait" and
                                                                float(kept.get("later_at") or 0) > now):
        return kept
    from core import parser_manga as PM
    pages = _card_paths(settings, comic, PM.LOCAL_PAGES)
    try:
        verdict = PM.confirm_pages(settings, [comic.title], pages, stop_check=stop_check)
    except PM.Pace as pace:
        verdict = PM.paced(pace.wait)
    if not verdict.state:
        return {"state": "wait"}                        # stopped half-way: asked again next round
    record = {"state": verdict.state, "url": verdict.url, "title": verdict.title, "by": verdict.by, "how": verdict.how,
              "at": int(now), "later_at": int(verdict.later_at or 0)}
    _check_put(comic.key, record)
    _LOG.info("comic %s: %s — %s", comic.key, verdict.state, verdict.by)
    return record


# ── (v5.44.113) «Проверить сейчас» and what the settings page says ──────────────────────────────────────────────
def check_now(settings: dict) -> "object":
    """«Настройки → Галерея → Проверить сейчас» (the owner: «и как мне перепроверить архив?»): the parser's folder looked
    at now, then rounds until nothing is left (the comics of the gallery, the parser's manga) — a task of the pages
    with its journal; :class:`ComicError` when there is nothing to do it with."""
    from core import manga_library as M, page_jobs
    if not M.manga_root(settings):
        raise ComicError("Не выбрана папка манги («Настройки → Хранение и копии → Папка манги»)")
    if not enabled(settings):
        raise ComicError("«Комиксы и манга — в папку манги» выключено")
    settings = dict(settings)

    def run(say, stop_check):
        from core import manga_detect as MD
        root = str(settings.get("root") or "").strip()
        if root and Path(root).is_dir():
            say("Смотрю папку парсера — какие папки похожи на мангу…")
            with _LOOKING:
                titles = MD.look(settings, stop_check=stop_check, say=say)
            _STATE["looked_at"] = int(time.time())
            if stop_check():
                return {"stopped": True}
            say(f"Похожих на мангу в папке парсера: {len(titles)} — в папку манги идёт только подтверждённая сайтом "
                "манги (ComicInfo.xml, ссылка, тайтл с теми же страницами)")
        else:
            say("Папка парсера не выбрана — смотрю только комиксы галереи")
        moved, copied, rounds = [], [], 0
        while not stop_check():
            got = background_round(settings, stop_check=stop_check, wait=True, parser=True, say=say)
            rounds += 1
            for name in got["moved"]:
                say(f"Комикс из галереи → «{name}»")
            for name in got["copied"]:
                say(f"Манга из папки парсера → «{name}»")
            moved += got["moved"]
            copied += got["copied"]
            if got["no_room"]:
                say(got["no_room"])
                break
            if not got["more"] or rounds > 500:
                break
            pause = min(30.0, float(got.get("wait") or 0))     # (v5.44.125) the sites' searches of the hour
            while pause > 0 and not stop_check():
                time.sleep(min(0.5, pause))
                pause -= 0.5
        say(f"Готово: комиксов из галереи — {len(moved)}, манги из папки парсера — {len(copied)}"
            + ("" if moved or copied else " (нового нет)"))
        return {"moved": len(moved), "copied": len(copied), "stopped": bool(stop_check())}
    return page_jobs.start(TASK, "Комиксы и манга — в папку манги", run)


def status(settings: dict) -> dict:
    """What the settings page says: comics and manga moved so far, the manga of the parser's folder not copied yet
    (without looking into their pictures), the last look at the folder, the last «no room».  (v5.44.125) «not copied
    yet» — the folders ``core/parser_manga`` still checks or that wait for SauceNAO (a folder found «not a manga» is
    the parser's: not counted)."""
    from core import manga_detect as MD, parser_manga
    done = moved()
    comics = sum(1 for k, r in done.items() if isinstance(r, dict) and r.get("folder") and not k.startswith("root:"))
    copies = sum(1 for k, r in done.items() if isinstance(r, dict) and r.get("done") and k.startswith("root:"))
    root = str((settings or {}).get("root") or "").strip()
    waiting, looked = 0, 0
    if root:
        looked = int(MD.STORE.read().get("at") or 0)
        counts = parser_manga.counts(settings)
        waiting = int(counts.get("check") or 0) + int(counts.get("wait") or 0)
    return {"comics": comics, "copies": copies, "waiting": waiting, "looked_at": looked,
            "due": bool(root) and MD.STORE.due(root), "no_room": _STATE.get("no_room") or ""}


# ── for «Из архива» (its background finds out what the manga is) ─────────────────────────────────────────────────
def _first_pages(folder: Path) -> list:
    """Up to three first pictures of a folder (else of its first chapter's folder) — for SauceNAO."""
    from core.manga_library import natural_key
    from core.manhwa_library import Page
    try:
        items = sorted(folder.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return []
    pictures = [p for p in items if p.is_file() and p.suffix.lower() in _PICTURES and not p.stem.lower() == "cover"]
    if not pictures:
        for sub in (p for p in items if p.is_dir() and not p.name.startswith(".")):
            try:
                pictures = sorted((p for p in sub.iterdir() if p.is_file() and p.suffix.lower() in _PICTURES),
                                  key=lambda p: natural_key(p.name))
            except OSError:
                continue
            if pictures:
                break
    return [Page(str(p)) for p in pictures[:3]]


def archive_entries(settings: dict, library: dict) -> tuple:
    """(``manga_archive.entries``) The manga this module put into the manga folder as the titles «Из архива» looks up:
    (their entries, the keys «root:<id>» of the parser's titles copied — those are looked up as their copies)."""
    from core import manga_archive as A, manga_detect as MD, manhwa_download as D
    from core.manhwa_library import Page
    out, copied = [], set()
    refused = MD.not_manga()
    for key, record in moved().items():
        if not isinstance(record, dict) or not record.get("folder") or not record.get("done", True):
            continue
        if key.startswith("root:"):
            copied.add(key)
            sources = [str(x) for x in record.get("sources") or [] if x] or \
                ([str(record["source"])] if record.get("source") else [])
            if sources and all(MD._under(Path(x), refused) for x in sources):
                continue                                # (v5.44.116) «Это не манга» on its row: the parser's again

        folder = Path(str(record["folder"]))
        lib = library.get(os.path.normcase(os.path.abspath(str(folder))))
        if lib is None or not folder.is_dir():
            continue                                    # not in the library (yet): the next round
        info = A._info_of(folder)
        linked = D.read_source(folder) if (folder / D.LB_DIR / D.SOURCE_FILE).is_file() else {}
        title = str(info.get("title") or "").strip() or A.clean_name(folder.name) or folder.name
        cover = next((folder / f"cover{ext}" for ext in (".jpg", ".jpeg", ".png", ".webp")
                      if linked and (folder / f"cover{ext}").is_file()), None)
        out.append(A.Entry(key=key if key.startswith("root:") else f"comic:{key}", kind="manga", lib_id=lib[1],
                           path=str(folder), title=title, names=A.names_for(title, folder, info),
                           chapters=int(record.get("chapters") or 1), pages=int(record.get("pages") or 0),
                           linked=linked if linked.get("site") else {}, folder=True, hentai=A._hentai(info, linked),
                           pages_of=A._pictures(Page(str(cover)) if cover else None, _first_pages(folder)),
                           library=lib[0], where=folder.name,
                           why="из папки парсера" if key.startswith("root:") else "комикс из галереи"))
    return out, copied
