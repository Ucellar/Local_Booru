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

with :data:`MIN_PAGES` live picture cards at least.  The program's rule about minors holds here too: a comic with a
page that its site does not rate as safe and whose tags speak of minors (``core/filter.depicts_minors`` and e621's own
words, :data:`MINOR_TAGS`) is never taken — it stays where it is.

How (:func:`move`): the pages are copied — «<name>/001.jpg …» (a pool's by their place in the pool) — first into
``<manga folder>/.lb/copying/<key>`` (:func:`_staging`: the library does not see a half-made title; a copy cut short
goes on there), each copy read back and checked against its card's MD5; ``info.json`` — the name, the link (the pool on
e621), the cards' tags by kind; then the folder is renamed into place, and the cards go to the trash
(``library_lifecycle.move_to_trash``, reason :data:`REASON`): their files with them, «Вернуть» brings a card back (it is
not taken again: what was moved is kept by its cards), the manga's copy stays.

The manga of the parser's folder (``core/manga_detect``: its titles the parser does not parse — and since this version
also a folder of :data:`manga_detect.MANY_PAGES` pages by numbers whatever their sizes): the program never moves nor
removes the parser's files, so a title is copied (:func:`copy_title`): its pictures, its .zip/.cbz/.pdf and the files of
its description (a chapter deeper than the title's folder — a folder right in the copy: the manga folder reads chapters
one level down), checked the same way; a manhwa (tall pages) stays for its own folder; then the gallery's cards of the
same bytes (parsed before the program knew it was a manga) go to the trash with the same reason.  Not when the manga
folder's disk would keep less than :data:`KEEP_FREE` (said in the log).

What was moved — ``comic_manga.json`` in the settings folder ({key: {folder, title, at, pages, cards, done…}}): a
pool's pages that come later go into the same folder; a folder the person removed from the manga folder is not made
again.  The manga is then looked up on the sites of manga by the background of «Из архива» (``manga_archive.entries``
takes these folders — :func:`archive_entries`; a copied title of the parser's folder is known by its copy): the name,
the description, the cover and the missing chapters, as it does the parser's folder's titles.
"""
from __future__ import annotations

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

_LOG = logging.getLogger("local_booru.card_sets")

MIN_PAGES = 15                  # pictures of a comic at least (the owner: «если 15+ каринок в сборке то в мангу»)
COMIC_SHARE = 0.5               # …of a сборка: so many of them with a comic's tag
REASON = "moved_to_manga"       # the trash's reason (``trash_tools.LABELS``)
KEEP_NAME = "comic_manga.json"
NAME_MAX = 120
TAGS_MAX = 60                   # general tags written into info.json (the common ones first)
# e621's own tags of minors (its «young» — under age; «cub» — the same of a furry): with a page not rated safe — never
MINOR_TAGS = frozenset({"young", "cub", "child", "toddler", "infant", "preteen", "loli", "shota"})
SAFE = ("s", "g")               # the sites' ratings of a safe picture (e621 «s», Danbooru «g» and «s»)
_PICTURES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".jxl"}
_ARCHIVES = {".zip", ".cbz", ".pdf"}           # what the manga folder reads besides pictures (``manga_library``)
_DESCRIPTION = {".json", ".xml", ".txt", ".url", ".webloc"}     # a title's own files of its description
STAGING = "copying"             # <manga folder>/.lb/copying/<key's hash>: a copy not finished yet
ROUND_SECONDS = 45              # a round of the background works so long at most (the duties share one thread)…
ROUND_BYTES = 3 * 1024 ** 3     # …and copies so much of the parser's manga at most
KEEP_FREE = 10 * 1024 ** 3      # the manga folder's disk keeps so much free after a copy
TASK = "comic_manga"            # (v5.44.113) «Проверить сейчас» — a task of the pages (``core/page_jobs``)
_LOCK = threading.Lock()
_ROUND = threading.Lock()       # (v5.44.113) one round at a time: the duty and «Проверить сейчас» never copy together
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


def _sets(con, alive: set, taken: set, done: dict, *, set_id: int = 0, any_kind: bool = False) -> list:
    out: list = []
    where = "WHERE set_id=?" if set_id else "WHERE kind IN ('work', 'series')"
    for sid, kind, title in con.execute(f"SELECT set_id, kind, title FROM card_sets {where}",
                                        (int(set_id),) if set_id else ()).fetchall():
        key = f"set:{int(sid)}"
        if key in done and not set_id:
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


def _forbidden(con, comic: Comic) -> bool:
    """The program's rule: a page its site does not rate as safe, whose tags speak of minors."""
    from core.filter import depicts_minors
    ids = [i for i, _place in comic.cards]
    tags = _tags_of(con, ids)
    ratings: dict = {}
    for start in range(0, len(ids), 800):
        part = ids[start:start + 800]
        marks = ",".join("?" * len(part))
        for image_id, rating in con.execute(f"SELECT image_id, rating FROM site_posts WHERE state='ok' "
                                            f"AND image_id IN ({marks})", part):
            ratings.setdefault(int(image_id), set()).add(str(rating or ""))
    for image_id in ids:
        names = [name for name, _category in tags.get(image_id, [])]
        minor = depicts_minors(names) or any(n.strip().lower() in MINOR_TAGS for n in names)
        if not minor:
            continue
        said = ratings.get(image_id) or {""}
        if not said <= set(SAFE):                       # not rated, or rated not safe by a site
            return True
    return False


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
        return [c for c in out if not _forbidden(con, c)]


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
        if _forbidden(con, comic):
            raise ComicError("Эту сборку программа в мангу не переносит: на её страницах изображены несовершеннолетние")
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
        raise ComicError("Не выбрана папка манги («Настройки → Папки → Папка манги»)")
    root = Path(text)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _record(key: str) -> dict:
    record = moved().get(key)
    return record if isinstance(record, dict) else {}


def _place(root: Path, key: str, work: Path, name: str, record: dict) -> Path:
    """The finished copy renamed into place (remembered first: a copy whose rename was cut short is not made twice)."""
    final = _unique(root, name)
    _remember(key, {**record, "folder": str(final), "done": False})
    os.replace(work, final)
    _remember(key, {"done": True})
    try:
        (root / ".lb" / STAGING).rmdir()                # empty now: nothing is being copied
    except OSError:
        pass
    return final


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
             "pages": int(record.get("pages") or 0) + len(copied),
             "cards": sorted(set(before) | {int(r["id"]) for r in copied})}
    if kept is None:
        folder = _place(root, comic.key, work, comic.title, {**record, **fresh})
    else:
        folder = kept
        _remember(comic.key, fresh)
    result = move_to_trash(settings, copied, reason=REASON, tag_or_source=folder.name[:200])
    trashed = int(result.get("trashed_records") or 0)
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


# ── the manga of the parser's folder ──────────────────────────────────────────────────────────────────────────────
def _plan(path: Path) -> list:
    """[(file, its place in the copy)] of a title of the parser's folder: the pictures, archives and PDF the manga
    folder reads and the files of its description; a chapter deeper than the title's folder — a folder right in the
    copy («Vol 1/Chapter 3» → «Vol 1 Chapter 3»: the manga folder reads chapters one level down); ``.lb`` — its link
    («Из архива» linked it where it lies)."""
    if path.is_file():
        return [(path, Path(path.name))] if path.suffix.lower() in _ARCHIVES else []
    plan: list = []
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        rel = here.relative_to(path)
        if rel.parts and rel.parts[0] == ".lb":
            dirnames[:] = []
            plan += [(here / n, Path(".lb") / n) for n in sorted(filenames) if n.lower().endswith(".json")]
            continue
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") or (not rel.parts and d == ".lb"))
        chapter = " ".join(rel.parts)
        for name in sorted(filenames):
            ext = Path(name).suffix.lower()
            if ext in _PICTURES:
                plan.append((here / name, Path(chapter) / name if chapter else Path(name)))
            elif ext in _ARCHIVES:
                plan.append((here / name, Path(f"{chapter} {name}" if chapter else name)))
            elif ext in _DESCRIPTION and not chapter:
                plan.append((here / name, Path(name)))
    return plan


def _manhwa(settings: dict, found) -> bool:
    from core.manga_archive import _kind_of
    try:
        return _kind_of(Path(found.path), settings, found.first) == "manhwa"
    except Exception:  # noqa: BLE001 - a picture that does not open: not a strip then
        return False


def _root_titles(settings: dict) -> list:
    """The titles of the parser's folder (``manga_detect.STORE``) to copy: not copied before, not in the manga or
    manhwa folder already, not a manhwa."""
    from core import manga_detect as MD, manhwa_download as D
    root = str((settings or {}).get("root") or "").strip()
    if not root or not Path(root).is_dir():
        return []
    libraries = [os.path.normcase(os.path.abspath(r)) for r in D.roots(settings).values() if r]
    done = moved()
    out = []
    for found in MD.STORE.titles(root):
        record = done.get(f"root:{found.id}")
        if isinstance(record, dict) and (record.get("done") or record.get("manhwa") or record.get("skipped") ==
                                         f"{found.chapters}:{found.pages}"):
            continue
        here = os.path.normcase(os.path.abspath(str(found.path)))
        if any(here == lib or (here + os.sep).startswith(lib + os.sep) or (lib + os.sep).startswith(here + os.sep)
               for lib in libraries):
            continue
        if not Path(found.path).exists():
            continue
        if _manhwa(settings, found):                    # (v5.44.113) remembered: its pictures are not opened again
            _remember(f"root:{found.id}", {"manhwa": True, "source": str(found.path), "at": int(time.time())})
            continue
        out.append(found)
    return out


def copy_title(settings: dict, found, *, say=None, stop_check=None, deadline: float = 0.0) -> "dict | None":
    """A manga of the parser's folder copied into the manga folder (:func:`_plan`, each file read back and checked),
    then the gallery's cards of its pictures' bytes to the trash: {"folder", "name", "pages", "bytes", "trashed"}; None
    — cut short (by ``stop_check`` or ``deadline``: the copy goes on next time).  :class:`NoRoom`, :class:`ComicError`."""
    from core import manga_library as M
    say = say or (lambda _text: None)
    stop_check = stop_check or (lambda: False)
    root = _root_of(settings)
    path = Path(found.path)
    key = f"root:{found.id}"
    plan = _plan(path)
    shown = [t for _s, t in plan if t.suffix.lower() in _PICTURES | _ARCHIVES and t.parts[0] != ".lb"]
    if not shown:
        _remember(key, {"skipped": f"{found.chapters}:{found.pages}", "source": str(path), "at": int(time.time())})
        raise ComicError(f"«{path.name}»: нет страниц, которые читает папка манги (.cbr и .cb7 — пока нет)")
    work = _staging(root, key)
    size = 0
    for source, target in plan:
        try:
            if not (work / target).is_file():
                size += source.stat().st_size
        except OSError:
            continue
    if size and shutil.disk_usage(root).free - size < KEEP_FREE:
        raise NoRoom(f"«{path.name}» ({size / 1024 ** 3:.1f} ГБ): на диске папки манги останется меньше "
                     f"{KEEP_FREE // 1024 ** 3} ГБ — не копирую")
    pictures, copied = set(), 0
    for source, target in plan:
        if stop_check() or (deadline and time.monotonic() > deadline):
            return None
        place = work / target
        md5 = _kept_copy(source, place)
        if not md5:
            try:
                md5 = _copy_checked(source, place)
                copied += place.stat().st_size
            except OSError as exc:
                _LOG.warning("manga %s: %s not copied: %s", path, source, exc)
                continue
        if target.suffix.lower() in _PICTURES:
            pictures.add(md5)
    if not any((work / t).is_file() for t in shown):
        shutil.rmtree(work, ignore_errors=True)
        raise ComicError(f"«{path.name}»: ни один файл не скопировался")
    name = path.name if path.is_dir() else path.stem
    record = {"title": name, "at": int(time.time()), "kind": "root", "source": str(path),
              "pages": int(found.pages or len(pictures)), "chapters": int(found.chapters or 1)}
    folder = _place(root, key, work, name, record)
    trashed, cards = _trash_same(settings, pictures, folder.name)
    if cards:
        _remember(key, {"cards": cards})
    M.LIBRARY.invalidate()
    say(f"Манга из папки парсера «{name}» — в папке манги «{folder.name}»"
        + (f", её карточки в галерее — в корзину: {trashed}" if trashed else ""))
    _LOG.info("parser's manga %s → %s: %d files, %.1f MB copied, %d cards trashed", path, folder, len(plan),
              copied / 1e6, trashed)
    return {"folder": str(folder), "name": folder.name, "pages": len(pictures), "bytes": copied, "trashed": trashed,
            "page": page_of(settings, folder)}


def _trash_same(settings: dict, md5s: set, folder_name: str) -> tuple:
    """The gallery's live cards of these bytes (not of a сборка the person made) to the trash: (count, [ids])."""
    from core.database.connection import db
    from core.library_lifecycle import move_to_trash
    wanted = sorted(m.lower() for m in md5s if m)
    if not wanted:
        return 0, []
    rows = []
    with db(settings, readonly=True) as con:
        manual = _manual_cards(con)
        for start in range(0, len(wanted), 800):
            part = wanted[start:start + 800]
            marks = ",".join("?" * len(part))
            rows += [dict(r) for r in con.execute(f"SELECT * FROM images WHERE deleted=0 AND LOWER(hash_md5) "
                                                  f"IN ({marks})", part) if int(r["id"]) not in manual]
    if not rows:
        return 0, []
    result = move_to_trash(settings, rows, reason=REASON, tag_or_source=folder_name[:200])
    return int(result.get("trashed_records") or 0), sorted(int(r["id"]) for r in rows)


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


def background_round(settings: dict, *, stop_check=None, wait: bool = False) -> dict:
    """The comics of the gallery and the manga of the parser's folder to the manga folder (the background duty
    «comic_manga», :data:`ROUND_SECONDS` and :data:`ROUND_BYTES` at most): {"moved", "copied", "more", "no_room",
    "busy"}; (v5.44.113) one round at a time (``wait`` — for the one going, else "busy")."""
    if not _ROUND.acquire(blocking=wait):
        return {"moved": [], "copied": [], "more": False, "no_room": "", "busy": True}
    try:
        out = _round(settings, stop_check=stop_check)
    finally:
        _ROUND.release()
    _STATE.update({"at": int(time.time()), "moved": _STATE["moved"] + len(out["moved"]),
                   "copied": _STATE["copied"] + len(out["copied"]), "no_room": out["no_room"]})
    return out


def _round(settings: dict, *, stop_check=None) -> dict:
    stop_check = stop_check or (lambda: False)
    from core import manga_library as M
    out = {"moved": [], "copied": [], "more": False, "no_room": ""}
    if not enabled(settings) or not M.manga_root(settings):
        return out
    if _look_if_due(settings):
        out["more"] = True                              # the folder's titles are being looked at: copied next time
    deadline = time.monotonic() + ROUND_SECONDS
    for comic in candidates(settings):
        if stop_check() or time.monotonic() > deadline:
            out["more"] = True
            return out
        try:
            got = move(settings, comic)
        except (ComicError, OSError) as exc:
            _LOG.info("comic %s not moved: %s", comic.key, exc)
            continue
        out["moved"].append(got["name"])
    copied = 0
    if _LOOKING.locked():
        return out                                      # the parser's folder is being looked at: next time
    for found in _root_titles(settings):
        if stop_check() or time.monotonic() > deadline or copied > ROUND_BYTES:
            out["more"] = True
            break
        try:
            got = copy_title(settings, found, stop_check=stop_check, deadline=deadline)
        except NoRoom as exc:
            out["no_room"] = str(exc)
            _LOG.warning("parser's manga: %s", exc)
            break
        except (ComicError, OSError) as exc:
            _LOG.info("parser's manga %s not copied: %s", found.path, exc)
            continue
        if got is None:
            out["more"] = True
            break
        out["copied"].append(got["name"])
        copied += int(got.get("bytes") or 0)
    return out


# ── (v5.44.113) «Проверить сейчас» and what the settings page says ──────────────────────────────────────────────
def check_now(settings: dict) -> "object":
    """«Настройки → Галерея → Проверить сейчас» (the owner: «и как мне перепроверить архив?»): the parser's folder looked
    at now, then rounds until nothing is left (the comics of the gallery, the parser's manga) — a task of the pages
    with its journal; :class:`ComicError` when there is nothing to do it with."""
    from core import manga_library as M, page_jobs
    if not M.manga_root(settings):
        raise ComicError("Не выбрана папка манги («Настройки → Папки → Папка манги»)")
    if not enabled(settings):
        raise ComicError("«Комиксы и манга — в папку манги» выключено")
    settings = dict(settings)

    def run(say, stop_check):
        from core import manga_detect as MD
        root = str(settings.get("root") or "").strip()
        if root and Path(root).is_dir():
            say("Смотрю папку парсера — какие там тайтлы манги…")
            with _LOOKING:
                titles = MD.look(settings, stop_check=stop_check, say=say)
            _STATE["looked_at"] = int(time.time())
            if stop_check():
                return {"stopped": True}
            say(f"Манги в папке парсера: {len(titles)}")
        else:
            say("Папка парсера не выбрана — смотрю только комиксы галереи")
        moved, copied, rounds = [], [], 0
        while not stop_check():
            got = background_round(settings, stop_check=stop_check, wait=True)
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
        say(f"Готово: комиксов из галереи — {len(moved)}, манги из папки парсера — {len(copied)}"
            + ("" if moved or copied else " (нового нет)"))
        return {"moved": len(moved), "copied": len(copied), "stopped": bool(stop_check())}
    return page_jobs.start(TASK, "Комиксы и манга — в папку манги", run)


def status(settings: dict) -> dict:
    """What the settings page says: comics and manga moved so far, the manga of the parser's folder not copied yet
    (without looking into their pictures), the last look at the folder, the last «no room»."""
    from core import manga_detect as MD, manhwa_download as D
    done = moved()
    comics = sum(1 for k, r in done.items() if isinstance(r, dict) and r.get("folder") and not k.startswith("root:"))
    copies = sum(1 for k, r in done.items() if isinstance(r, dict) and r.get("done") and k.startswith("root:"))
    root = str((settings or {}).get("root") or "").strip()
    waiting, looked = 0, 0
    if root:
        data = MD.STORE.read()
        looked = int(data.get("at") or 0)
        libraries = [os.path.normcase(os.path.abspath(r)) for r in D.roots(settings).values() if r]
        for found in MD.STORE.titles(root):
            if isinstance(done.get(f"root:{found.id}"), dict):
                continue
            here = os.path.normcase(os.path.abspath(found.path))
            if not any(here == lib or (here + os.sep).startswith(lib + os.sep) for lib in libraries):
                waiting += 1
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
    from core import manga_archive as A, manhwa_download as D
    from core.manhwa_library import Page
    out, copied = [], set()
    for key, record in moved().items():
        if not isinstance(record, dict) or not record.get("folder") or not record.get("done", True):
            continue
        if key.startswith("root:"):
            copied.add(key)
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
