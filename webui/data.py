"""What the web pages read from the library (read-only; changes go through core functions)."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

from core import trash_tools
from core.database.connection import db
from core.tag_utils import normalize_tag, should_hide_tag

_LOG = logging.getLogger("local_booru.webui")

ORDERS = {"newest": "Сначала новые", "oldest": "Сначала старые", "parsed_newest": "Недавно добавленные",
          "filename": "По имени файла"}


LONG_VIDEO = "COALESCE(i.duration,0) > ?"           # a video card longer than the tab «Видео» takes (data.video_limit)
NOT_LONG_VIDEO = "COALESCE(i.duration,0) <= ?"
VIDEO_SECTION = "videos"                             # the tab «Видео»: the long videos of the library
# Sections that show every card whatever its length: what is in the trash or in Брак is looked at to be dealt with.
ALL_LENGTHS = {"trash", "no_match"}


def video_limit(settings: dict | None) -> int:
    """Seconds: a video card longer than this is on the tab «Видео», not in the gallery (0 — every video is in the
    gallery).  «Настройки → Видео»."""
    try:
        return max(0, int((settings or {}).get("video_tab_seconds", 60)))
    except (TypeError, ValueError):
        return 60


def is_long_video(settings: dict | None, item: dict | None) -> bool:
    """(v5.44.121) A card of a long video (the tab «Видео»: longer than :func:`video_limit`) — played once, its place
    kept; a short one goes round as a gif does."""
    limit = video_limit(settings)
    try:
        return limit > 0 and float((item or {}).get("duration") or 0) > limit
    except (TypeError, ValueError):
        return False


def section_filter(section: str, settings: dict | None = None) -> tuple[str, list[str], list]:
    """(bucket for repository search, extra WHERE, its params) for a gallery section.  The gallery leaves out the
    videos longer than ``video_limit`` (they are on the tab «Видео»: section ``videos``), except in the trash and in
    Брак."""
    section = str(section or "all")
    limit = video_limit(settings)
    if section == VIDEO_SECTION:
        return "all", [LONG_VIDEO], [limit if limit > 0 else 10 ** 9]
    length = ([NOT_LONG_VIDEO], [limit]) if limit > 0 and section not in ALL_LENGTHS else ([], [])
    if section == "favorites":
        return "all", ["i.favorite=1", *length[0]], length[1]
    if section in {"inbox", "found", "downloaded", "no_match", "trash", "archive"}:
        return section, length[0], length[1]
    return "all", length[0], length[1]


_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v")

# The words of the section «Брак» (links above its pictures, webui/pages/nomatch.py): word -> (the Брак entries
# that are meant, whether the card is one of them or not, the label).  A card is matched by its file (the Брак
# copy) or the input file of its entry; cards without an entry count as «не найдено» and «вид не определён».
BRAK_WORDS = {
    "брак:ненайдено": ("reason='source_only'", False, "Брак: не найдено"),
    "брак:источник": ("reason='source_only'", True, "Брак: источник без тегов"),
    "брак:фото": ("visual_status='real'", True, "Брак: похоже на фото"),
    "брак:рисунок": ("visual_status='booru'", True, "Брак: похоже на рисунок"),
    "брак:неясно": ("visual_status IN ('real','booru')", False, "Брак: вид не определён"),
}


def brak_condition(word: str) -> tuple[str, list]:
    """SQL condition on ``images i`` for a word of :data:`BRAK_WORDS`."""
    entries, inside, _label = BRAK_WORDS[word]
    paths = (f"SELECT media_path FROM no_match_items WHERE active=1 AND {entries} "
             f"UNION SELECT original_path FROM no_match_items WHERE active=1 AND {entries}")
    return (f"(i.bucket IN ('no_match','downloaded_no_match') AND i.path {'IN' if inside else 'NOT IN'} ({paths}))", [])


def split_query(query: str) -> tuple[str, list[str], list, list[str]]:
    """Tags of a search and its non-tag conditions.

    Returns (tags, SQL conditions, their parameters, labels for the page).  Understood besides tags:
    ``md5:<hash>``, ``id:<number>``, ``type:video|gif|image``, the words of the section «Брак» (``брак:фото`` …),
    why a card is in the trash (``корзина:<code of core.trash_tools.LABELS>``) and the size/rating shorthands of
    the old gallery (``size:+5mb``, ``width:1920``, ``height:-1080``, ``rating:4``, ``tags:0`` …).
    """
    words, where, params, labels = [], [], [], []
    for word in str(query or "").split():
        low = word.lower()
        if low.startswith("md5:") and len(low) > 4:
            where.append("(i.hash_md5=? OR EXISTS(SELECT 1 FROM image_site_md5_aliases a WHERE a.image_id=i.id AND a.site_md5=?))")
            params += [low[4:], low[4:]]
            labels.append(f"MD5 {low[4:]}")
        elif low.startswith("id:") and low[3:].isascii() and low[3:].isdigit():
            where.append("i.id=?")
            params.append(min(int(low[3:]), 2 ** 63 - 1))     # SQLite numbers are 64-bit
            labels.append(f"номер {low[3:]}")
        elif low in BRAK_WORDS:
            condition, more = brak_condition(low)
            where.append(condition)
            params += more
            labels.append(BRAK_WORDS[low][2])
        elif low.startswith(trash_tools.WORD) and len(low) > len(trash_tools.WORD):
            code = low[len(trash_tools.WORD):]
            condition, more = trash_tools.condition(code)
            where.append(condition)
            params += more
            labels.append(f"в корзине: {trash_tools.label(code)}")
        elif low in {"type:video", "type:gif", "type:image"}:
            kind = low[5:]
            if kind == "video":
                where.append("(i.is_video=1 OR " + " OR ".join("LOWER(i.path) LIKE ?" for _ in _VIDEO_EXTS) + ")")
                params += ["%" + ext for ext in _VIDEO_EXTS]
                labels.append("только видео")
            elif kind == "gif":
                where.append("LOWER(i.path) LIKE ?")
                params.append("%.gif")
                labels.append("только GIF")
            else:
                where.append("COALESCE(i.is_video,0)=0 AND " + " AND ".join("LOWER(i.path) NOT LIKE ?" for _ in (*_VIDEO_EXTS, ".gif")))
                params += ["%" + ext for ext in (*_VIDEO_EXTS, ".gif")]
                labels.append("только картинки")
        else:
            words.append(word)
    rest = " ".join(words)
    if ":" in rest or any("а" <= ch.lower() <= "я" for ch in rest):
        from core.search.human_query_parser import parse_query, to_sql_conditions
        parsed = parse_query(rest)
        if parsed.filters:
            more_where, more_params = to_sql_conditions(parsed.filters)
            where += more_where
            params += more_params
            labels += [f.to_display() for f in parsed.filters]
            rest = " ".join([*parsed.tags, *parsed.unknown_tokens])
    return rest, where, params, labels


def _filters(settings: dict, query: str, section: str) -> tuple[str, str, list[str], list]:
    tags, where, params, _labels = split_query(query)
    bucket, extra, extra_params = section_filter(section, settings)
    return tags, bucket, [*extra, *where], [*extra_params, *params]


def long_videos_found(settings: dict, query: str, *, source: str = "all") -> int:
    """How many videos of a gallery search are on the tab «Видео» instead (0 when the tab takes none)."""
    if video_limit(settings) <= 0 or not str(query or "").strip():
        return 0
    tags, bucket, extra, params = _filters(settings, query, VIDEO_SECTION)
    return _counted(settings, tags, source, bucket, extra, params, False)


def collapses(settings: dict | None, section: str) -> bool:
    """Whether a сборка of cards (core/card_sets.py) is one tile in this section: everywhere in the gallery but the
    trash and Брак (cards there are dealt with one by one), unless switched off in «Настройки»."""
    from core.card_sets import tiles_enabled
    return tiles_enabled(settings or {}) and str(section or "all") not in ALL_LENGTHS and section != VIDEO_SECTION


def mark_tiles(settings: dict, items: list) -> list:
    """Give the cards that stand for a сборка its number and size (``set_id``, ``set_count``)."""
    from core.card_sets import tile_counts
    try:
        tiles = tile_counts(settings, [int(it["id"]) for it in items])
    except sqlite3.Error as exc:                     # an old base: the tiles without counts
        _LOG.debug("tiles of the page: %s", exc)
        tiles = {}
    for item in items:
        set_id, count = tiles.get(int(item["id"]), (0, 0))
        item["set_id"], item["set_count"] = set_id, count
    return items


def search_page(settings: dict, query: str, *, section: str = "all", source: str = "all", order: str = "newest",
                page: int = 1, per_page: int = 42) -> tuple[list, int]:
    from core.database.repository import enrich_items, search_items
    tags, bucket, extra, params = _filters(settings, query, section)
    collapse = collapses(settings, section)
    total = _counted(settings, tags, source, bucket, extra, params, collapse)
    # (v5.44.40) tiles vs files: a сборка is one tile but many files — the gallery shows both, so count the files too
    # (only when sets are collapsed; without collapsing a tile is a file, and in the trash / Брак sets never collapse)
    files_total = total if not collapse else _counted(settings, tags, source, bucket, extra, params, False)
    offset = max(0, (page - 1) * per_page)
    items = search_items(settings, tags, source, bucket, limit=per_page, offset=offset,
                         order=order if order in ORDERS else "newest", extra_where=extra, extra_params=params,
                         collapse_sets=collapse)
    enrich_items(settings, items, tag_source=source or "all")
    if collapse:
        mark_tiles(settings, items)
    return items, total, files_total


def count_tiles(settings: dict, query: str, *, section: str = "all", source: str = "all") -> int:
    """How many tiles a search has (a сборка — one tile where the section shows it so): the phone's «12 из 1 240»."""
    tags, bucket, extra, params = _filters(settings, query, section)
    return _counted(settings, tags, source, bucket, extra, params, collapses(settings, section))


def base_mark(settings: dict) -> tuple:
    """What changes whenever anything is written to the base: the size and the time of its file and of its journal
    (every commit goes into the journal; a journal put back into the file changes the file).  Two looks at the disk,
    nothing read."""
    from core.database.connection import db_path
    path = db_path(settings)
    out = [str(path)]
    for name in (str(path), str(path) + "-wal"):
        try:
            st = os.stat(name)
            out.append((st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


class PageCounts:
    """(v5.44.152) How many cards a search of the gallery has, kept while the base is as it was.

    Every page of the gallery counted its search anew — twice (tiles and files): on 40 thousand cards the whole
    table of the cards read for each count.  The number is the same until something is written to the base
    (:func:`base_mark`), so the next page of the same search, and the way back to it, take it from here; ``TTL`` — a
    count is not believed longer than this whatever the mark says; a change of the library the pages are told of
    (``TAG_COUNTS.invalidate`` — it follows the tag counts) forgets them all at once."""

    TTL = 120.0
    KEPT = 300

    def __init__(self):
        self._lock = threading.Lock()
        self._kept: OrderedDict = OrderedDict()

    def get(self, settings: dict, key: tuple, count) -> int:
        mark = base_mark(settings)
        now = time.monotonic()
        with self._lock:
            item = self._kept.get(key)
            if item is not None and item[1] == mark and now - item[2] < self.TTL:
                self._kept.move_to_end(key)
                return item[0]
        value = int(count())
        with self._lock:
            self._kept[key] = (value, mark, now)
            self._kept.move_to_end(key)
            while len(self._kept) > self.KEPT:
                self._kept.popitem(last=False)
        return value

    def forget(self) -> None:
        with self._lock:
            self._kept.clear()

    invalidate = forget                   # a follower of the tag counts (``Kept.followers``)


PAGE_COUNTS = PageCounts()


def _counted(settings: dict, tags, source, bucket, extra, params, collapse: bool) -> int:
    from core.database.repository import count_search_items
    key = (str(tags), str(source), str(bucket), tuple(extra or ()), tuple(params or ()), bool(collapse))
    return PAGE_COUNTS.get(settings, key, lambda: count_search_items(
        settings, tags, source, bucket, extra_where=extra, extra_params=params, collapse_sets=collapse))


def item_at(settings: dict, query: str, *, section: str, source: str, order: str, index: int) -> dict | None:
    """The card at position ``index`` (0-based) of a search — for «previous / next» on the post page (a сборка is one
    place: its card the gallery shows)."""
    if index < 0:
        return None
    from core.database.repository import search_items
    tags, bucket, extra, params = _filters(settings, query, section)
    rows = search_items(settings, tags, source, bucket, limit=1, offset=index,
                        order=order if order in ORDERS else "newest", extra_where=extra, extra_params=params,
                        collapse_sets=collapses(settings, section))
    return rows[0] if rows else None


class Kept:
    """A count over the whole library for the pages, counted once and then kept.

    When the copy is older than ``ttl`` the pages get the old numbers at once while a background thread counts again,
    so no page waits for the count after the first one.  ``invalidate`` (the library changed) makes the next page
    start a recount; a kept count may have ``followers`` that are made stale with it.
    """

    THREAD = "webui-kept"
    FILE = ""                 # (v5.44.152) the file of the cache folder that keeps the count between the runs
    SAVE_EVERY = 300.0        # seconds between two savings of a count that changes all the time (a parser's run)

    def __init__(self, ttl: float = 60.0, followers: tuple = ()):
        self.ttl = ttl
        self.followers = tuple(followers)
        self._saved_at = None                 # time.monotonic() of the last saving (None — not saved by this run)
        self._saved_mark = None
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._loaded_at = 0.0
        self._db = ""
        self._refreshing = False
        self._generation = 0                  # +1 on every change of the library (invalidate)
        self._by_name = None                  # what _load gave

    def get(self, settings: dict):
        from core.database.connection import db_path
        key = str(db_path(settings))
        with self._lock:
            if self._db == key and self._loaded_at:
                if time.time() - self._loaded_at >= self.ttl and not self._refreshing:
                    self._refreshing = True
                    threading.Thread(target=self._refresh, args=(dict(settings), key, self._generation),
                                     name=self.THREAD, daemon=True).start()
                return self._by_name
        with self._load_lock:                     # the first count: one thread counts, the others wait
            with self._lock:
                if self._db == key and self._loaded_at:
                    return self._by_name
                generation = self._generation
            # (v5.44.152) the count of the last run first: the first page after a start does not wait for the whole
            # table of tags to be read from the disk.  The numbers are counted anew behind it, always: whether the
            # base is as it was when they were kept is not something its files say for sure (closing the program
            # alone rewrites them)
            kept = self._restore(key)
            if kept is not None:
                with self._lock:
                    self._by_name, self._db = kept, key
                    self._loaded_at = 1.0
                    start = not self._refreshing
                    if start:
                        self._refreshing = True
                if start:
                    threading.Thread(target=self._refresh, args=(dict(settings), key, generation),
                                     name=self.THREAD, daemon=True).start()
                return kept
            data = self._load(settings)
            with self._lock:
                self._by_name, self._db = data, key
                self._loaded_at = time.time() if generation == self._generation else 1.0
            threading.Thread(target=self._keep, args=(dict(settings), key, data), name=self.THREAD + "-keep",
                             daemon=True).start()
            return data

    # ── between the runs ──
    def _place(self) -> Path | None:
        if not self.FILE:
            return None
        from core.paths import CACHE_DIR
        return Path(CACHE_DIR) / self.FILE

    @staticmethod
    def _pack(data):
        return data

    @staticmethod
    def _unpack(data):
        return data

    def _restore(self, key: str):
        """The count the last run kept for this base (None — there is none)."""
        place = self._place()
        if place is None:
            return None
        try:
            kept = json.loads(place.read_text(encoding="utf-8"))
            if not isinstance(kept, dict) or kept.get("v") != 1 or kept.get("db") != key:
                return None
            return self._unpack(kept["data"])
        except (OSError, ValueError, KeyError, TypeError) as exc:      # no file, a file cut short: counted as before
            if not isinstance(exc, FileNotFoundError):
                _LOG.info("%s not read (%s) — counted anew", place.name, exc)
            return None

    def _keep(self, settings: dict, key: str, data, *, often: bool = True) -> None:
        """The count written for the next run; ``often`` False — not more than once in ``SAVE_EVERY``."""
        place = self._place()
        if place is None:
            return
        try:
            mark = base_mark(settings)
            now = time.monotonic()
            with self._lock:
                recent = self._saved_at is not None and now - self._saved_at < self.SAVE_EVERY
                if mark == self._saved_mark or (not often and recent):
                    return
                self._saved_mark, self._saved_at = mark, now
            from core.file_safety import atomic_write_bytes
            atomic_write_bytes(place, json.dumps({"v": 1, "db": key, "data": self._pack(data)},
                                                 ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (OSError, ValueError, TypeError) as exc:                 # a cache that is not written: counted next time
            _LOG.info("%s not kept: %s", place.name, exc)

    def _refresh(self, settings: dict, key: str, generation: int) -> None:
        data = None
        try:
            data = self._load(settings)
        except Exception as exc:  # archive drive gone for a moment, a locked base: keep the old numbers
            _LOG.warning("tag counts not refreshed: %s", exc)
        finally:
            with self._lock:
                self._refreshing = False
                if data is not None and self._db == key:
                    self._by_name = data
                    # Changed while counting (a card trashed meanwhile): these numbers are already old.
                    self._loaded_at = time.time() if generation == self._generation else 1.0
            if data is not None:
                self._keep(settings, key, data, often=False)

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            if self._loaded_at:
                self._loaded_at = 1.0          # stale: the next page starts a recount
        for follower in self.followers:
            follower.invalidate()

    @staticmethod
    def _load(settings: dict):
        raise NotImplementedError


class SourceHosts(Kept):
    """The sites of the gallery's «Источники» box with how many live cards each has (the 30 biggest).  (v5.44.58)
    Counted on every page of the gallery before: on 100 thousand cards with 200 thousand source links 0.2–0.3 s of
    the 0.4 s of a page — now once a minute, and after a change of the library (it follows the tag counts)."""

    THREAD = "webui-source-hosts"
    FILE = "gallery_sites.json"

    @staticmethod
    def _unpack(data):
        return [(str(host), int(count)) for host, count in data]

    @staticmethod
    def _load(settings: dict) -> list:
        with db(settings, readonly=True) as con:
            rows = con.execute("""
                SELECT s.host AS host, COUNT(DISTINCT x.image_id) AS c FROM image_sources x
                JOIN sources s ON s.id=x.source_id JOIN images i ON i.id=x.image_id AND i.deleted=0
                GROUP BY s.host ORDER BY c DESC LIMIT 30
            """).fetchall()
        return [(str(r["host"]), int(r["c"])) for r in rows]


class TagCounts(Kept):
    """How many live cards carry each tag.

    Counted once (about 0.25 s for 100 thousand pictures with 2.4 million tag links) and then kept (:class:`Kept`).
    """

    THREAD = "webui-tag-counts"
    FILE = "gallery_tag_counts.json"

    @staticmethod
    def _unpack(data):
        return {str(norm): (str(name), str(category), int(count)) for norm, (name, category, count) in data.items()}

    @staticmethod
    def _load(settings: dict) -> dict:
        with db(settings, readonly=True) as con:
            # Count over the (tag_id, image_id) index without touching images, then take away the cards
            # in the trash (few): ten times faster than joining every link with its card.
            totals = dict(con.execute("SELECT tag_id, COUNT(*) FROM image_tags GROUP BY tag_id").fetchall())
            dead = dict(con.execute("""
                SELECT it.tag_id, COUNT(*) FROM images i JOIN image_tags it ON it.image_id=i.id
                WHERE i.deleted=1 GROUP BY it.tag_id
            """).fetchall())
            rows = con.execute("SELECT id, normalized_name, name, COALESCE(NULLIF(category,''),'general') FROM tags").fetchall()
        out = {}
        for tag_id, norm, name, category in rows:
            count = int(totals.get(tag_id, 0)) - int(dead.get(tag_id, 0))
            if count > 0:
                out[str(norm)] = (str(name), str(category).lower(), count)
        return out


SOURCE_HOSTS = SourceHosts()
TAG_COUNTS = TagCounts(followers=(SOURCE_HOSTS, PAGE_COUNTS))      # a change of the library makes all three stale


def sidebar_tags(settings: dict, items: list, limit: int = 60) -> dict[str, list[tuple[str, int]]]:
    """Tags of the cards on this page with their library-wide counts, grouped by category."""
    counts = TAG_COUNTS.get(settings)
    seen: dict[str, str] = {}
    for item in items:
        for category, names in (item.get("tag_groups") or {}).items():
            for name in names:
                norm = normalize_tag(name)
                if norm and norm not in seen:
                    seen[norm] = str(category or "general").lower()
    ranked = []
    for norm, category in seen.items():
        name, global_cat, count = counts.get(norm, (norm, category, 0))
        cat = category if category != "general" else global_cat
        if should_hide_tag(name, cat, settings):
            continue
        ranked.append((count, norm, name, cat))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for count, _norm, name, cat in ranked[:limit]:
        groups[cat].append((name, count))
    return groups


def autocomplete(settings: dict, prefix: str, limit: int = 12) -> list[dict]:
    """Tags starting with ``prefix`` (then containing it), most used first."""
    prefix = normalize_tag(prefix).lower()
    if not prefix:
        return []
    counts = TAG_COUNTS.get(settings)
    starts, contains = [], []
    for norm, (name, category, count) in counts.items():
        low = norm.lower()
        if low.startswith(prefix):
            starts.append((count, norm, name, category))
        elif len(prefix) >= 3 and prefix in low:
            contains.append((count, norm, name, category))
    starts.sort(key=lambda x: (-x[0], x[1]))
    contains.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for count, norm, name, category in (starts + contains)[:limit]:
        out.append({"tag": norm, "name": name, "category": category, "count": count})
    return out


def tag_rows(settings: dict, *, name_filter: str = "", category: str = "") -> list:
    """The library's tags (norm, name, category, cards) — of ``category`` (all: ""), whose name matches ``name_filter``
    (``webui/tag_lists.matcher``: * — any text, without it — the text anywhere); in no order."""
    from webui.tag_lists import matcher
    match = matcher(name_filter)
    return [(norm, name, cat, count) for norm, (name, cat, count) in TAG_COUNTS.get(settings).items()
            if (not category or cat == category) and match(norm)]


def tag_categories(settings: dict) -> dict:
    """category -> how many tags of the library have it."""
    out: dict = defaultdict(int)
    for _name, cat, _count in TAG_COUNTS.get(settings).values():
        out[cat] += 1
    return dict(out)


def post_detail(settings: dict, image_id: int) -> dict | None:
    from core.database.repository import enrich_items
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT * FROM images WHERE id=?", (int(image_id),)).fetchone()
        if row is None:
            return None
        item = dict(row)
        meta = con.execute("SELECT site, post_url, file_url FROM raw_metadata WHERE image_id=?", (int(image_id),)).fetchone()
        item["raw"] = dict(meta) if meta else {}
        # Tags that belong to no site set (old library tags, manual tags): shown as their own group.
        from core.image_notes import notes_for_image, text_regions_for_image
        try:
            item["notes"] = notes_for_image(con, int(image_id)) + text_regions_for_image(con, int(image_id))
        except sqlite3.OperationalError:          # a library from before the notes tables
            item["notes"] = []
        loose = con.execute("""
            SELECT t.name, COALESCE(NULLIF(t.category,''),'general') AS category FROM image_tags it
            JOIN tags t ON t.id=it.tag_id
            WHERE it.image_id=? AND it.tag_id NOT IN (SELECT tag_id FROM image_source_tags WHERE image_id=?)
            ORDER BY t.name COLLATE NOCASE
        """, (int(image_id), int(image_id))).fetchall()
        if int(item.get("deleted") or 0):          # why it is in the trash (the post page says it)
            item["trash_reason"], item["trash_target"] = trash_tools.reason_of(con, int(image_id))
    enriched = {"id": int(image_id)}
    enrich_items(settings, [enriched])
    item["tag_groups"] = enriched.get("tag_groups") or {}
    item["tag_groups_by_source"] = enriched.get("tag_groups_by_source") or {}
    item["sources"] = enriched.get("sources") or []
    loose_groups: dict[str, list[str]] = defaultdict(list)
    if item["tag_groups_by_source"]:
        for r in loose:
            loose_groups[str(r["category"]).lower()].append(str(r["name"]))
    item["loose_groups"] = dict(loose_groups)
    return item


def image_id_by_path(settings: dict, path: str) -> int:
    """The live card of the file at ``path`` (0 — none): what a download became."""
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT id FROM images WHERE path=? AND deleted=0 ORDER BY id DESC LIMIT 1",
                          (str(Path(path)),)).fetchone()
    return int(row[0]) if row else 0


def file_path_of(settings: dict, image_id: int) -> Path | None:
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT path FROM images WHERE id=?", (int(image_id),)).fetchone()
    if row is None:
        return None
    path = Path(str(row["path"] or ""))
    return path if path.is_file() else None


def file_and_version_of(settings: dict, image_id: int) -> tuple[Path | None, str]:
    """The card's file (None when it is not on disk) and the version of its picture addresses
    (``media_version``): the pages put it into ``/media/<id>?v=…``, and only an address with the current
    version may be kept by the browser."""
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT path, hash_md5, size_bytes, mtime_ns FROM images WHERE id=?",
                          (int(image_id),)).fetchone()
    if row is None:
        return None, ""
    path = Path(str(row["path"] or ""))
    return (path if path.is_file() else None), media_version(dict(row))


def media_version(item: dict) -> str:
    """What makes ``/media/<id>``, ``/thumb/<id>`` and ``/poster/<id>`` of one picture differ from those of
    another picture under the same number.  Numbers come back: a card deleted for good frees its number for
    the next one, and «Сбросить базу» starts them again from 1 while the window still keeps pictures by
    address — the old picture would be shown for the new card.  The md5 of the file (or its size and time
    when there is no md5) goes into the address, so another file is another address."""
    md5 = str(item.get("hash_md5") or "").strip().lower()
    if len(md5) >= 12 and all(ch in "0123456789abcdef" for ch in md5[:12]):
        return md5[:12]
    try:
        return f"{int(item.get('size_bytes') or 0):x}-{int(item.get('mtime_ns') or 0):x}"
    except (TypeError, ValueError):
        return "0-0"


# (v5.44.37) a video's preview and poster are its average frame now (core/image_safe.AVERAGE): their addresses are
# other than before, or the window keeps the old pictures it was allowed to keep for a year
VIDEO_PICTURE_MARK = ".a300"


def picture_version(version: str, path) -> str:
    """The version in ``/thumb/<id>`` and ``/poster/<id>`` of a card whose file is ``path``: a video's carries
    :data:`VIDEO_PICTURE_MARK`."""
    from core.image_safe import VIDEO_SUFFIXES
    return version + VIDEO_PICTURE_MARK if Path(str(path or "")).suffix.lower() in VIDEO_SUFFIXES else version


def media_url(kind: str, item: dict) -> str:
    """``/media/<id>?v=…`` (``kind`` "media", "thumb" or "poster") for a card dict with id, hash_md5, size_bytes,
    mtime_ns (and path: a video's preview and poster have their own version — :func:`picture_version`)."""
    version = media_version(item)
    if kind in ("thumb", "poster"):
        version = picture_version(version, item.get("path"))
    return f"/{kind}/{int(item['id'])}?v={version}"


def media_file_of(settings: dict, image_id: int, *, video: bool = False) -> tuple[Path | None, str]:
    """The card's file for actions that hand it to another program (open, play, clipboard, drag):
    (path, "") or (None, why not).  Only picture and video types: a card whose file is something
    else (a site served ``1234.hta``) is shown, but never opened."""
    from core.media_utils import CARD_FILE_EXTS, CARD_VIDEO_EXTS
    path = file_path_of(settings, image_id)
    if path is None:
        return None, "Файла этой карточки нет на диске"
    allowed = CARD_VIDEO_EXTS if video else CARD_FILE_EXTS
    if path.suffix.lower() not in allowed:
        return None, f"Файл «{path.suffix or 'без расширения'}» — не картинка и не видео, галерея его не открывает"
    return path, ""


def source_hosts(settings: dict) -> list[tuple[str, int]]:
    """The sites of the library with their live cards, the biggest 30 (kept: :data:`SOURCE_HOSTS`)."""
    return list(SOURCE_HOSTS.get(settings))


def random_in_search(settings: dict, query: str = "", *, section: str = "all", source: str = "all",
                     order: str = "newest") -> tuple[int, int] | None:
    """«Случайная» of a search (no search — of the whole library): (a random card of it, its place in the search
    for «previous / next») or None."""
    import random
    from core.database.repository import count_search_items
    tags, bucket, extra, params = _filters(settings, query, section)
    total = count_search_items(settings, tags, source, bucket, extra_where=extra, extra_params=params,
                               collapse_sets=collapses(settings, section))
    if total <= 0:
        return None
    index = random.randrange(total)
    item = item_at(settings, query, section=section, source=source, order=order, index=index)
    return (int(item["id"]), index) if item else None


def result_ids(settings: dict, query: str, *, section: str = "all", source: str = "all") -> list[int]:
    """Card numbers of a whole search (for «экспорт тегов и источников этих результатов»)."""
    from core.database.repository import search_items
    tags, bucket, extra, params = _filters(settings, query, section)
    rows = search_items(settings, tags, source, bucket, limit=None, order="newest", extra_where=extra, extra_params=params)
    return [int(r["id"]) for r in rows]
