"""(v5.44.164) Pages of manga lying loose among the parser's pictures.

The owner (12.11.2026): «у меня есть картинки без папки но мангой и один хуй все проходит проверку snao так что проблем
не будет если и на мангу будет каждая проверяться».  ``core/parser_manga.py`` looks at the folders that look like a
manga; a page saved by itself — in a folder of anything — was a picture no booru site has: Брак, with «есть источник без
тегов» at best.  SauceNAO, the last step of the parser's search, answers about every index at once — its indexes of
manga too (``manga_pictures.INDEXES``): the same answer says «a page of the manga X, chapter N».  No search is spent on
it.

* **Noted** — :func:`seen` (``ParserEngine._finish_miss``: the picture is in Брак already): the manga its answer names
  (``manga_pictures.manga_hits`` — past the program's gate, ``gates.title``: what SauceNAO says of each, its name), the
  best first; the pictures of one name are one manga's (:class:`Store`: ``parser_loose.json`` in the settings folder).
  The journal says «ЖДЁТ: …» in the tab «Манга» of the page «Парсер».  A picture put off («отложен»: a site did not
  answer) after SauceNAO named a manga — :func:`hold`: the next run does not ask SauceNAO again, so what it named is
  kept for the picture's end.
* **Confirmed** — only by a site of manga of the program, in the background (:func:`background_round`, a part of the
  duty «comic_manga»; the sites' searches at ``parser_manga``'s pace of the hour): the name searched on the sites
  (``manhwa_search.search_now`` — its tiles past the gate), and the picture compared by pHash with **every page of the
  chapter SauceNAO named** of the titles of that very name (``core/parser_loose_pages.py``: ``check``, ``pages_of``
  — a title's ``title()`` ends with the gate too): a page alike — confirmed.  Two pictures SauceNAO is sure of
  (``parser_manga.SURE`` %) of one name whose title a site has — confirmed too (as two pages of a folder are).  A
  name alone never; a picture SauceNAO named no chapter of, or whose chapter the site has not (or sells) — not
  confirmed: the picture stays in Брак as it was («Это манга» on its row is the owner's word).  No site of manga
  answered — it waits and is asked again.
* **A confirmed manga** goes to «Манга» whole: its download into the queue of «Загрузки» (``manhwa_queue`` — the
  title found again by its link: ``manhwa_download.look``, the gate once more; «Докачивать главы, которых нет в папке»
  of the page «Парсер» off — nothing is downloaded and the pictures stay), begun only while nobody is at the computer
  (``background_duties.person_busy`` — the program's own downloads do not take the processor from the person).  When
  the title is in the library (``catalog_have``), the cards of its pictures leave Брак — to the trash, «перенесено в
  мангу» («Вернуть» brings each back; ``parser_manga.trash_paths``: only a card still in Брак, never one the person put
  into a сборка of his own).  The parser's own files are only read: never moved, changed or removed.
* **Брак as it was before this version** — :func:`_sweep`: its pictures whose «источник без тегов» is SauceNAO's index
  of manga are asked of SauceNAO once more (the answer kept there has no name of the manga) — within the searches of
  the day knowing manga may take (``manga_pictures.ask_one``; half of them at most: :func:`_ask_cap`), never while the
  parser runs.
* **The page** — rows of the block «Манга в исходной папке» (:func:`rows`; ``webui/pages/parser_manga.py``), a
  picture by the row's id (:func:`cover_of`), the owner's word (:func:`say_manga`, :func:`say_not`, «Скачать сейчас»
  — :func:`download_now`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path

from core import gates as G, manga_detect as MD, parser_loose_pages as LP, parser_manga as PM

_LOG = logging.getLogger("local_booru.manga")

STATE_FILE = "parser_loose.json"
MANGA, NOT, WAIT = PM.MANGA, PM.NOT, PM.WAIT
ASK, GONE = "ask", "gone"       # a picture of Брак to be asked of SauceNAO again; one that is not there any more
PREFIX = PM.LOOSE               # a row's key on the page: «loose:<the manga's id>»
HITS = 3                        # the manga one answer of SauceNAO names, kept at most
FILES = 4                       # pictures of one manga compared with a site's pages a round at most
ROUND_SECONDS = 15              # a round works so long at most (the duty «comic_manga» shares one thread)
SWEEP_EVERY = 6 * 3600          # Брак is looked through for the old answers of SauceNAO so often
ASKS_A_ROUND = 2                # pictures of Брак asked of SauceNAO again a round at most
QUEUE_TRIES = 3                 # a title's download begun so many times at most…
QUEUE_AGAIN = 12 * 3600         # …the next one so much after the one before (it ended, and the title is not there)
LISTED = 12                     # pictures of a row named on the page
HELD_FOR = 90 * 86400           # an answer kept for a picture put off is forgotten after so long

_INDEX = re.compile(r"Index #(\d+)")


def gid_of(key: str) -> str:
    """A manga's id on the page (its name's key is as long as its name)."""
    return hashlib.md5(str(key).encode("utf-8")).hexdigest()[:12]


# ── what was noted ──────────────────────────────────────────────────────────────────────────────────────────────────
class Store:
    """``parser_loose.json``: {"files": {a picture's path (``manga_detect._norm``): {"path", "media" (its copy in
    Брак, when the parser worked on that), "md5", "hits" [{"name", "part", "link", "sim", "index"}], "group" (its
    manga: the key of the best name), "state" ("" — not compared yet, "manga", "not", "ask", "gone"), "how" ("page",
    "sure", "hand"), "page" («Глава 12, стр. 7»), "why", "done" (nothing more to do with it), "trashed" (its card went
    to the trash), "tries", "at"}}, "groups": {a name's key: {"name", "state"
    ("" — to check, "wait", "manga", "not"), "how", "by", "url", "title", "label", "site", "key", "adult", "later_at",
    "tries", "searched_at", "seen_url", "seen_title", "seen_label", "queued_at", "queue_tries", "placed", "fill",
    "trashed", "open", "hand", "at"}}, "asked": {a path of Брак asked again that named no manga: when}, "held": {a
    picture put off after SauceNAO answered: {"hits", "at"} — :func:`hold`}, "swept_at", "ask_at", "ask_day",
    "ask_n"}."""

    def __init__(self):
        self._lock = threading.RLock()
        self.rev = 0                                   # +1 a change (the page asks again)

    def file(self) -> Path:
        from core.paths import SETTINGS_DIR
        return Path(SETTINGS_DIR) / STATE_FILE

    def read(self) -> dict:
        from core.file_safety import read_json_kept
        data = read_json_kept(self.file(), dict, what="parser loose pages")
        if not isinstance(data, dict):
            data = {}
        for name in ("files", "groups", "asked", "held"):
            if not isinstance(data.get(name), dict):
                data[name] = {}
        return data

    def _write(self, data: dict) -> None:
        from core.file_safety import write_json_kept
        write_json_kept(self.file(), json.dumps(data, ensure_ascii=False, indent=1), dict)
        self.rev += 1

    def change(self, fn):
        """``fn(data)`` under the lock, written after; what it returned."""
        with self._lock:
            data = self.read()
            out = fn(data)
            self._write(data)
            return out


STORE = Store()


def _open_hits(hits: list) -> list:
    """The manga an answer names that the program's gate lets by (``gates.title`` — what SauceNAO says of each: its
    name) and whose name names something: the best first, one a name, :data:`HITS` at most."""
    from core import manga_clues as C, manhwa_sources as S
    out, had = [], set()
    for hit in sorted((h for h in hits or [] if isinstance(h, dict)), key=lambda h: -float(h.get("sim") or 0)):
        name = " ".join(str(hit.get("name") or "").split())[:200]
        key = S.name_key(name)
        if not key or key in had or not S.names_of(name) or C.junk_name(name):
            continue
        if G.title("saucenao", names=[name]):
            continue                                    # (closed: neither noted nor said)
        had.add(key)
        out.append({"name": name, "part": " ".join(str(hit.get("part") or "").split())[:80],
                    "link": str(hit.get("link") or "")[:300], "sim": round(float(hit.get("sim") or 0), 1),
                    "index": int(hit.get("index") or 0)})
        if len(out) >= HITS:
            break
    return out


def _told(name: str, hit: dict) -> str:
    _tome, number = LP.chapter_of(hit.get("part"))
    return (f"«{name}» — SauceNAO: страница манги «{hit['name']}»" + (f", глава {number}" if number else "")
            + f" ({float(hit.get('sim') or 0):.0f} %)")


def _note(data: dict, path: str, md5: str, kept: list, media: str, now: int) -> str:
    """A picture and the manga its answer names put into ``data``; the journal's line ("" — nothing new to say)."""
    from core import manhwa_sources as S
    best = kept[0]
    key = S.name_key(best["name"])
    norm = MD._norm(path)
    group = data["groups"].setdefault(key, {"name": best["name"], "at": now, "state": ""})
    rec = dict(data["files"].get(norm) or {})
    known = rec.get("state") in (MANGA, NOT) and rec.get("group") == key
    rec.update(path=str(path), md5=str(md5 or rec.get("md5") or ""), hits=kept, group=key, at=now)
    if media:
        rec["media"] = str(media)
    if not known:
        rec["state"] = ""
        for old in ("how", "page", "why"):
            rec.pop(old, None)
    data["files"][norm] = rec
    data["asked"].pop(norm, None)
    told = _told(Path(str(path)).name, best)
    if group.get("hand") == NOT:
        rec["state"] = NOT
        rec["why"] = "вы сказали: «Это не манга»"
        return ""
    if known:
        return ""
    if group.get("state") == MANGA:
        title = group.get("title") or group["name"]
        if best["sim"] >= PM.SURE or group.get("hand") == MANGA:      # (the owner's word is of all its pictures)
            rec.update(state=MANGA, how="sure" if best["sim"] >= PM.SURE else "hand")
            return PM.line("МАНГА", f"{told} — манга «{title}» уже подтверждена: картинка уйдёт из «Брака»")
        return PM.line("ЖДЁТ", f"{told} — манга «{title}» уже подтверждена; эту картинку сверю со страницами главы")
    if group.get("state") == NOT:
        group["state"] = ""                             # one more picture of it: compared with the title's pages too
    return PM.line("ЖДЁТ", f"{told} — сверю со страницами тайтла на сайтах манги: совпадёт — тайтл в «Мангу», "
                           "картинка уйдёт из «Брака»")


_HELD: dict = {"paths": None}           # the pictures whose answer is kept for their end (read once a run of the program)


def _held() -> set:
    with STORE._lock:
        if _HELD["paths"] is None:
            _HELD["paths"] = set(STORE.read()["held"])
        return _HELD["paths"]


def hold(settings: dict, path: str, hits: list) -> None:
    """(``ParserEngine._finish_miss``) A picture put off («отложен»: a site did not answer) after SauceNAO named a
    manga for it: a later run does not ask SauceNAO about it again (``reverse_journal`` keeps its answers) — what it
    named is kept for the picture's end (:func:`seen` takes it then)."""
    if not PM.enabled(settings) or not hits or Path(str(path)).suffix.lower() not in MD.IMAGES:
        return
    kept = _open_hits(hits)
    if not kept:
        return
    norm, now = MD._norm(path), int(time.time())
    with STORE._lock:
        _held().add(norm)
        STORE.change(lambda data: data["held"].update({norm: {"hits": kept, "at": now}}))


def seen(settings: dict, path: str, md5: str, hits: list, *, media: str = "") -> str:
    """(``ParserEngine._finish_miss``) A picture no booru site has, whose answer of SauceNAO names a manga (``hits`` —
    ``manga_pictures.manga_hits``; none — the answer kept of a run that put the picture off, :func:`hold`): noted, to
    be confirmed by the sites of manga in the background.  ``media`` — its copy in Брак, when that is the file the
    parser worked on.  The journal's line ("" — nothing to say: the switch is off, not a picture, no manga named,
    the gate closed what it names, the owner said «Это не манга» of that manga)."""
    if not PM.enabled(settings) or Path(str(path)).suffix.lower() not in MD.IMAGES:
        return ""
    norm = MD._norm(path)
    was_held = norm in _held()
    if not hits and not was_held:
        return ""                                       # (every picture of Брак comes here: nothing read for it)
    now = int(time.time())

    def put(data: dict) -> str:
        kept_before = data["held"].pop(norm, None) or {}
        kept = _open_hits(hits or kept_before.get("hits") or [])
        if not kept:                                    # (nothing of it is to be asked: Брак's sweep leaves it too)
            data["asked"][norm] = now
            return ""
        return _note(data, str(path), md5, kept, media, now)
    with STORE._lock:
        _held().discard(norm)
        return STORE.change(put)


# ── a picture and a site's pages ────────────────────────────────────────────────────────────────────────────────────
def _file_of(settings: dict, rec: dict) -> "Path | None":
    """The file a picture is read from: the parser's own (only read), else its copy in Брак."""
    for name in (rec.get("path"), rec.get("media")):
        if name and Path(str(name)).is_file():
            return Path(str(name))
    try:
        from core.nomatch_db import entry_for_path
        entry = entry_for_path(settings, str(rec.get("path") or "")) or {}
    except Exception as exc:  # noqa: BLE001 - the base busy: not read this time
        _LOG.info("parser loose: the copy in Брак not looked up: %s", type(exc).__name__)
        return None
    copy = str(entry.get("media_path") or "")
    return Path(copy) if copy and Path(copy).is_file() else None


def _bytes(settings: dict, rec: dict) -> bytes:
    path = _file_of(settings, rec)
    if path is None:
        return b""
    try:
        return path.read_bytes()
    except OSError as exc:
        _LOG.info("parser loose: a picture not read: %s", type(exc).__name__)
        return b""


# ── a confirmed manga: into «Манга», its pictures out of Брак ───────────────────────────────────────────────────────
def _person_busy() -> bool:
    try:
        from core.background_duties import person_busy
        return bool(person_busy())
    except Exception as exc:  # noqa: BLE001 - not known who is there: as if somebody were
        _LOG.info("parser loose: who is at the computer not known: %s", type(exc).__name__)
        return True


def _going(url: str) -> bool:
    """The title's download waits in the queue of «Загрузки» or goes now."""
    from core import manhwa_queue as Q
    current = Q.current_record() or {}
    return bool(Q.queued(url, "")) or current.get("url") == url


def _open_link(settings: dict, site: str, tkey: str) -> str:
    """The page of the title in the library ("" — not found by its link's site)."""
    from core import manhwa_download as D, manhwa_queue as Q
    try:
        linked = D.linked_folder(settings, site, tkey) if site and tkey else None
        return Q.open_link(settings, str(linked[1]), linked[0]) if linked else ""
    except Exception as exc:  # noqa: BLE001 - the library busy: the row has no link this time
        _LOG.info("parser loose: the title's page not found: %s", type(exc).__name__)
        return ""


def _in_brak(settings: dict, paths: list) -> "set | None":
    """The ones of ``paths`` (the parser's files) whose card is still a card of Брак; None — the base was not read."""
    from core.database.connection import db
    paths = [str(p) for p in paths if p]
    out: set = set()
    try:
        with db(settings, readonly=True) as con:
            for start in range(0, len(paths), 500):
                part = paths[start:start + 500]
                marks = ",".join("?" * len(part))
                out |= {str(r[0]) for r in con.execute(
                    "SELECT o.original_path FROM source_originals o JOIN images i ON i.id=o.image_id "
                    f"WHERE o.original_path IN ({marks}) AND +i.deleted=0 AND +i.bucket='no_match'", part)}
    except Exception as exc:  # noqa: BLE001 - the base busy: asked again next round
        _LOG.info("parser loose: the cards of Брак not read: %s", type(exc).__name__)
        return None
    return out


def _place(settings: dict, key: str, say, *, asked: bool = False) -> dict:
    """A confirmed manga put into «Манга»: its download begun (once nobody is at the computer; never twice at a
    time; ``asked`` — «Скачать сейчас», the person's own word: now, and whatever was tried before), and — when the
    title is in the library — the cards of its pictures out of Брак (the trash, «перенесено в мангу»).
    {"queued", "trashed", "fill" — what its download is doing now}."""
    from core import catalog_have, manhwa_queue as Q, manhwa_sources as S
    out = {"queued": 0, "trashed": 0}
    data = STORE.read()
    group = dict(data["groups"].get(key) or {})
    if group.get("state") != MANGA or not group.get("url"):
        return out
    mine = [(norm, rec) for norm, rec in data["files"].items()
            if rec.get("group") == key and rec.get("state") == MANGA and not rec.get("done")]
    title = str(group.get("title") or group.get("name") or "")
    url, site, tkey = str(group["url"]), str(group.get("site") or ""), str(group.get("key") or "")
    now = int(time.time())
    values: dict = {}
    have = catalog_have.downloaded("manga", settings)
    there = have.holds([(site, tkey)] if site and tkey else [], S.names_of(title, str(group.get("name") or "")))
    done: dict = {}                     # a picture nothing more is to be done with -> whether its card went to the trash
    still: set = set()                  # the ones whose card is in Брак after all (the person's own сборка, no backup)
    if there:
        gone = 0
        if mine:
            paths = [str(rec.get("path") or "") for _n, rec in mine]
            was = _in_brak(settings, paths)
            if was:
                gone = PM.trash_paths(settings, paths, title, brak_only=True)
            left = _in_brak(settings, paths) if was is not None else None
            for norm, rec in mine if left is not None else []:        # (the base not read: next round)
                path = str(rec.get("path") or "")
                if path not in left:
                    done[norm] = path in was            # (not in Брак before either: a site found it since)
                elif int(rec.get("tries") or 0) >= 3:
                    done[norm] = False                  # (it stays there: not tried every round for ever)
                else:
                    still.add(norm)
        if not group.get("placed") or mine:
            values.update(placed=True, trashed=int(group.get("trashed") or 0) + gone,
                          fill={"state": "done" if group.get("queued_at") else "have"},
                          open=group.get("open") or _open_link(settings, site, tkey))
            say(PM.line("МАНГА", f"«{title}» — " + ("скачивается в «Мангу»" if _going(url) else "в «Манге»")
                        + (f"; {PM.plural(gone, PM.PICTURES)} из «Брака» — в корзину (можно вернуть)" if gone else "")))
        out["trashed"] = gone
    elif not PM.fill_on(settings):
        values["fill"] = {"state": "off"}
    elif _going(url):
        values["fill"] = {"state": "queued"}
    elif not asked and int(group.get("queue_tries") or 0) >= QUEUE_TRIES:
        values["fill"] = {"state": "failed"}
    elif not asked and group.get("queued_at") and now - int(group["queued_at"]) < QUEUE_AGAIN:
        values["fill"] = {"state": "again", "at": int(group["queued_at"]) + QUEUE_AGAIN}
    elif not asked and _person_busy():
        values["fill"] = {"state": "held"}
    else:
        record = {"url": url, "title": title, "branch": "", "where": "", "follow": True, "helpers": True,
                  "chapters": None, "hentai": bool(group.get("adult"))}

        def work(say_, stop_, record=record):
            return Q.work(record, settings, say_, stop_)
        Q.start(f"Скачать: {title}", work, record=record, about=PREFIX + gid_of(key), title=title, count=0)
        values.update(queued_at=now, queue_tries=int(group.get("queue_tries") or 0) + 1, fill={"state": "queued"})
        out["queued"] = 1
        say(PM.line("МАНГА", f"«{title}» — скачиваю в «Мангу» (видно в «Загрузках»); её картинки уйдут из «Брака», "
                             "когда тайтл появится в «Манге»"))
    if values and any(group.get(k) != v for k, v in values.items()) or mine and there:
        def put(found: dict) -> None:
            kept = found["groups"].get(key)
            if kept is not None:
                kept.update(values)
            if there:
                for norm, _rec in mine:
                    if norm not in found["files"]:
                        continue
                    if norm in done:
                        found["files"][norm].update(done=1, trashed=1 if done[norm] else 0)
                    elif norm in still:
                        found["files"][norm]["tries"] = int(found["files"][norm].get("tries") or 0) + 1
        STORE.change(put)
    out["fill"] = str((values.get("fill") or group.get("fill") or {}).get("state") or "")
    return out


# ── Брак as it was before this version ──────────────────────────────────────────────────────────────────────────────
def _sweep(settings: dict) -> int:
    """Брак's pictures whose «источник без тегов» is SauceNAO's index of manga and that this module has not seen —
    noted to be asked of SauceNAO once more (what Брак keeps of the answer has no name of the manga).  How many."""
    from core import manga_pictures as P
    from core.database.connection import db
    try:
        with db(settings, readonly=True) as con:
            rows = [(str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), float(r[3] or 0)) for r in con.execute(
                "SELECT original_path, media_path, source_label, source_similarity FROM no_match_items "
                "WHERE +active=1 AND reason='source_only' AND source_label LIKE 'SauceNAO Index #3%'")]
    except Exception as exc:  # noqa: BLE001 - the base busy (or older than the columns): looked through next time
        _LOG.info("parser loose: Брак not looked through: %s", type(exc).__name__)
        return 0
    wanted = []
    for path, media, label, sim in rows:
        index = _INDEX.search(label)
        if index and int(index.group(1)) in P.INDEXES and sim >= P.SIMILAR and path \
                and Path(path).suffix.lower() in MD.IMAGES:
            wanted.append((path, media))
    now = int(time.time())

    def put(data: dict) -> int:
        added = 0
        for path, media in wanted:
            norm = MD._norm(path)
            if norm in data["files"] or norm in data["asked"]:
                continue
            data["files"][norm] = {"path": path, "media": media, "md5": "", "hits": [], "group": "", "state": ASK,
                                   "at": now}
            added += 1
        for norm in [k for k, v in data["held"].items() if now - int((v or {}).get("at") or 0) > HELD_FOR]:
            data["held"].pop(norm, None)                # (put off long ago and never ended: found since, or gone)
        data["swept_at"] = now
        return added
    with STORE._lock:
        added = int(STORE.change(put) or 0)
        _HELD["paths"] = None                           # (read again: the old ones are gone)
    return added


def _ask_cap(settings: dict) -> int:
    """Pictures of Брак asked of SauceNAO again a day at most: half of the searches knowing manga may take (the
    folders of the parser and «Из архива» keep the other half)."""
    from core import manga_pictures as P
    return max(1, P.per_day(settings) // 2)


def _ask(settings: dict, asker, stop_check, deadline: float, say) -> int:
    """Брак's old pictures asked of SauceNAO again (:data:`ASKS_A_ROUND` a round, :func:`_ask_cap` a day, never while
    the parser runs): the manga an answer names — noted as :func:`seen` notes; none — remembered, never asked again.
    How many were asked."""
    from core import manga_pictures as P
    from core.parser import jobs as parser_jobs
    data = STORE.read()
    today = time.strftime("%Y-%m-%d")
    done = int(data.get("ask_n") or 0) if data.get("ask_day") == today else 0
    asks = [(norm, rec) for norm, rec in data["files"].items() if rec.get("state") == ASK]
    if not asks or parser_jobs.is_running() or float(data.get("ask_at") or 0) > time.time() \
            or done >= _ask_cap(settings):
        return 0
    asked = 0
    for norm, rec in asks[:ASKS_A_ROUND]:
        if stop_check() or time.monotonic() > deadline or done + asked >= _ask_cap(settings):
            break
        picture = _bytes(settings, rec)
        got = asker.sauce(picture) if picture else {"bad": "картинки нет"}
        if got.get("sent"):
            asked += 1
        if got.get("stopped"):
            break
        if got.get("off") or got.get("later"):
            until = int(got.get("until") or 0) or int(time.time()) + (86400 if got.get("off") else PM.LATER)
            STORE.change(lambda found, until=until: found.update(ask_at=until))
            break
        kept = _open_hits(P.manga_hits(got.get("data") or {})) if "data" in got else []
        now = int(time.time())

        def put(found: dict, norm=norm, rec=rec, kept=kept, now=now) -> str:
            found["files"].pop(norm, None)
            if not kept:
                found["asked"][norm] = now              # (it names no manga: never asked again)
                return ""
            return _note(found, str(rec.get("path") or ""), "", kept, str(rec.get("media") or ""), now)
        text = STORE.change(put)
        if text:
            say(text.replace(" — SauceNAO:", " (из «Брака») — SauceNAO:", 1))
    if asked:
        def count(found: dict) -> None:
            found["ask_n"] = (int(found.get("ask_n") or 0) if found.get("ask_day") == today else 0) + asked
            found["ask_day"] = today
        STORE.change(count)
    return asked


# ── the background ──────────────────────────────────────────────────────────────────────────────────────────────────
_ROUND = threading.Lock()


def _due(data: dict, now: float) -> list:
    """The manga to look at now, the cheapest first: the ones confirmed with pictures not compared yet (no search),
    then the new ones and the ones whose wait is over — the ones SauceNAO is surest of first."""
    waiting: dict = {}
    for rec in data["files"].values():
        if rec.get("state") == "" and rec.get("group"):
            waiting[rec["group"]] = max(waiting.get(rec["group"], 0.0), LP.sim_of(rec, rec["group"]))
    out = []
    for key, group in data["groups"].items():
        if key not in waiting or group.get("hand") == NOT:
            continue
        state = group.get("state") or ""
        if state == MANGA and float(group.get("files_at") or 0) > now:
            continue                                    # (its title was not given a while ago: asked later)
        if state == MANGA or state == "" or state == WAIT and float(group.get("later_at") or 0) <= now:
            out.append((state != MANGA, -waiting[key], int(group.get("at") or 0), key))
    return [key for *_order, key in sorted(out)]


def _one(settings: dict, asker, key: str, deadline: float, tell) -> dict:
    """One manga checked and what came of it kept: {"state", "more", "wait"}."""
    data = STORE.read()
    group = dict(data["groups"].get(key) or {})
    every = [rec for rec in data["files"].values() if rec.get("group") == key and rec.get("state") in ("", MANGA)]
    mine = sorted(((norm, rec) for norm, rec in data["files"].items()
                   if rec.get("group") == key and rec.get("state") == ""), key=lambda x: -LP.sim_of(x[1], key))
    probes, files = [], {}
    for norm, rec in mine[:FILES]:
        mark = PM._phash(_bytes(settings, rec))
        if not mark:
            files[norm] = {"state": GONE, "why": "картинка не прочиталась"}
        elif LP.flat(mark):
            files[norm] = {"state": NOT, "why": "почти пустая картинка — не сверяется"}
        else:
            probes.append((norm, rec, mark))
    got = {"values": {}, "files": {}}
    if probes:
        sure = sum(1 for rec in every if LP.sim_of(rec, key) >= PM.SURE)
        got = LP.check(settings, asker, key, group, probes, sure, deadline)
    elif group.get("state") != MANGA and len(mine) <= FILES:
        got["values"] = {"state": NOT, "how": "nothing", "by": "её картинки не прочитались или пусты"}
    files.update(got.get("files") or {})
    values = dict(got.get("values") or {})
    more = bool(got.get("more"))
    if values.get("state") == NOT and len(mine) > FILES and values.get("seen_url"):
        # more pictures than a round compares: these are not its pages, the rest are compared with the title found
        # (its link is kept: no search) — the manga is «not» only when none of them is a page of it
        for norm, _rec, _mark in probes:
            files.setdefault(norm, {"state": NOT, "why": str(values.get("by") or "")})
        values = {"state": "", **{k: v for k, v in values.items() if k.startswith("seen_") or k == "searched_at"}}
        more = True
    elif len(mine) > FILES and values.get("state") in (MANGA, None):
        more = True                                     # (the rest of a confirmed manga's pictures: next round)
    if values or files:
        def put(found: dict) -> None:
            kept = found["groups"].get(key)
            if kept is not None and values:
                kept.update(values)
            for norm, new in files.items():
                if norm in found["files"]:
                    found["files"][norm].update(new)
            if values.get("state") == NOT:              # the ones not compared this round are not a manga's either
                for rec in found["files"].values():
                    if rec.get("group") == key and rec.get("state") == "":
                        rec.update(state=NOT, why=str(values.get("by") or ""))
        STORE.change(put)
    name = str(group.get("name") or "")
    number = len(every) or len(mine)
    count = PM.plural(number, PM.PICTURES)
    state = values.get("state", "")
    if state == MANGA:
        tell(PM.line("МАНГА", f"«{name}» — {count} среди отдельных картинок: {values.get('by')}"))
    elif state == NOT:
        tell(PM.line("НЕ МАНГА", f"«{name}» — {count} среди отдельных картинок: {values.get('by')} — "
                                 + ("остаётся" if number == 1 else "остаются") + " в «Браке»"))
    elif state == WAIT:
        tell(PM.line("ЖДЁТ", f"«{name}» — {count} среди отдельных картинок: {values.get('by')} — проверю позже"))
    return {"state": state, "more": more, "wait": float(got.get("wait") or 0)}


def background_round(settings: dict, *, stop_check=None, seconds: float = ROUND_SECONDS, say=None) -> dict:
    """(``comic_manga``'s duty, «Проверить сейчас») The pages of manga among the loose pictures: the confirmed manga
    put into «Манга» (their downloads begun, their pictures out of Брак once the title is there), the new ones and the
    ones whose wait is over compared with the sites' pages, Брак's old pictures asked of SauceNAO again — for
    ``seconds`` at most; one round at a time.  ``say`` — a line of a journal (the program's log too).
    {"checked", "manga", "queued", "trashed", "asked", "more", "wait", "busy"}."""
    out = {"checked": 0, "manga": [], "queued": 0, "trashed": 0, "asked": 0, "more": False, "wait": 0.0, "busy": False}
    said = say or (lambda _text: None)

    def tell(text: str) -> None:
        _LOG.info("parser loose: %s", text)
        said(text)

    settings = dict(settings or {})
    if not PM.enabled(settings):
        return out
    if not _ROUND.acquire(blocking=False):
        out["busy"] = True
        return out
    try:
        stop_check = stop_check or (lambda: False)
        deadline = time.monotonic() + max(1.0, float(seconds))
        data = STORE.read()
        if time.time() - float(data.get("swept_at") or 0) >= SWEEP_EVERY:
            _sweep(settings)
            data = STORE.read()
        if not data["files"]:
            return out

        def place(key: str) -> None:
            got = _place(settings, key, tell)
            out["queued"] += got["queued"]
            out["trashed"] += got["trashed"]
        pending = {rec.get("group") for rec in data["files"].values()
                   if rec.get("state") == MANGA and not rec.get("done")}
        for key, group in list(data["groups"].items()):
            if group.get("state") == MANGA and (key in pending or not group.get("placed")):
                if stop_check() or time.monotonic() > deadline:
                    out["more"] = True
                    return out
                place(key)
        asker = None
        for key in _due(STORE.read(), time.time()):
            if stop_check() or time.monotonic() > deadline:
                out["more"] = True
                return out
            asker = asker or PM.background_asker(settings, stop_check)
            got = _one(settings, asker, key, deadline, tell)
            out["more"] = out["more"] or got["more"]
            out["wait"] = max(out["wait"], got["wait"])
            if got["state"]:
                out["checked"] += 1
            if got["state"] == MANGA:
                out["manga"].append(str((STORE.read()["groups"].get(key) or {}).get("title") or key))
                place(key)
            elif got["state"] == "" and (STORE.read()["groups"].get(key) or {}).get("state") == MANGA:
                place(key)                              # (more pictures of a manga confirmed before)
        if not (stop_check() or time.monotonic() > deadline):
            asker = asker or PM.background_asker(settings, stop_check)
            out["asked"] = _ask(settings, asker, stop_check, deadline, tell)
        return out
    finally:
        _ROUND.release()


# ── the owner's word, the page ──────────────────────────────────────────────────────────────────────────────────────
def _key_of(data: dict, row_key: str) -> str:
    """The manga a row's key names ("" — none)."""
    wanted = str(row_key or "")
    wanted = wanted[len(PREFIX):] if wanted.startswith(PREFIX) else wanted
    return next((key for key in data["groups"] if gid_of(key) == wanted), "")


def say_manga(settings: dict, row_key: str) -> dict:
    """«Это манга» of a row not confirmed: the owner's word — the title of that name a site of manga has is the
    manga (downloaded by the background as a confirmed one; its pictures leave Брак when it is in «Манга»)."""
    from core import manhwa_sources as S
    data = STORE.read()
    key = _key_of(data, row_key)
    group = data["groups"].get(key) if key else None
    if group is None:
        return {"ok": False, "error": "Такой строки в списке нет — откройте страницу заново"}
    url = str(group.get("url") or group.get("seen_url") or "")
    source, tkey = S.source_for(url)
    if source is None:
        return {"ok": False, "error": "На сайтах манги программы тайтла с таким названием не нашлось — скачать нечего"}

    def put(found: dict) -> None:
        kept = found["groups"].get(key)
        if kept is None:
            return
        kept.update(hand=MANGA, state=MANGA, how="hand", by="вы сказали: «Это манга»", url=url, site=source.name,
                    key=tkey, title=kept.get("title") or kept.get("seen_title") or kept.get("name"),
                    label=kept.get("label") or kept.get("seen_label") or source.label,
                    adult=bool(kept.get("adult") or kept.get("seen_adult") or source.adult_only), later_at=0)
        for rec in found["files"].values():
            if rec.get("group") == key and rec.get("state") in ("", NOT):
                rec.update(state=MANGA, how="hand")
                rec.pop("why", None)
    STORE.change(put)
    return {"ok": True}


def download_now(settings: dict, row_key: str) -> dict:
    """«Скачать сейчас» of a row whose download waits for the person to leave, or did not come: begun now."""
    data = STORE.read()
    key = _key_of(data, row_key)
    group = data["groups"].get(key) if key else None
    if group is None or group.get("state") != MANGA:
        return {"ok": False, "error": "Такой строки в списке нет — откройте страницу заново"}
    got = _place(settings, key, lambda text: _LOG.info("parser loose: %s", text), asked=True)
    if got["queued"]:
        return {"ok": True, "message": "Скачивание в очереди — видно в «Загрузках»"}
    words = {"off": "Выключено «Докачивать главы, которых нет в папке» — включите его выше",
             "queued": "Тайтл уже скачивается — видно в «Загрузках»",
             "have": "Тайтл уже в «Манге»", "done": "Тайтл уже в «Манге»"}
    return {"ok": False, "error": words.get(got.get("fill") or "", "Скачивание не началось — попробуйте ещё раз")}


def say_not(settings: dict, row_key: str) -> dict:
    """«Это не манга» of a row: its pictures are pictures — they stay in Брак, nothing more is asked about them; the
    ones gone to the trash already can be brought back there, a download begun is not stopped."""
    data = STORE.read()
    key = _key_of(data, row_key)
    if not key:
        return {"ok": False, "error": "Такой строки в списке нет — откройте страницу заново"}

    def put(found: dict) -> None:
        kept = found["groups"].get(key)
        if kept is None:
            return
        kept.update(hand=NOT, state=NOT, how="hand", by="вы сказали: «Это не манга»", later_at=0)
        for rec in found["files"].values():
            if rec.get("group") == key and not rec.get("trashed") and rec.get("state") in ("", MANGA, NOT):
                rec.update(state=NOT, why="вы сказали: «Это не манга»")
    STORE.change(put)
    return {"ok": True}


def cover_of(row_key: str):
    """The first picture of a row's manga that is still there — by the row's key, never a path."""
    from core.manga_library import PageRef
    data = STORE.read()
    key = _key_of(data, row_key)
    if not key:
        return None
    mine = sorted((rec for rec in data["files"].values() if rec.get("group") == key),
                  key=lambda rec: (rec.get("state") != MANGA, -LP.sim_of(rec, key)))
    for rec in mine:
        for name in (rec.get("path"), rec.get("media")):
            if name and Path(str(name)).is_file():
                return PageRef(str(name))
    return None


def rows(settings: dict) -> list:
    """The rows of the block «Манга в исходной папке» of the manga found among the loose pictures — shaped as
    ``parser_manga.rows``' (``loose`` marks them): a manga, its pictures, what the check said, where they went."""
    data = STORE.read()
    by_group: dict = {}
    for rec in data["files"].values():
        if rec.get("group") and rec.get("state") not in (ASK, GONE):
            by_group.setdefault(rec["group"], []).append(rec)
    out = []
    for key, group in data["groups"].items():
        mine = sorted(by_group.get(key) or [], key=lambda rec: (rec.get("state") != MANGA, -LP.sim_of(rec, key)))
        if not mine:
            continue
        state = group.get("state") or ""
        state = state if state in (MANGA, NOT, WAIT) else "check"
        accepted = [rec for rec in mine if rec.get("state") == MANGA]
        size = PM.plural(len(mine), PM.PICTURES)
        name = str(group.get("title") or group.get("name") or "")
        work = {"key": key, "name": name, "state": "" if state == "check" else state, "how": group.get("how", ""),
                "by": group.get("by", ""), "sim": max((LP.sim_of(rec, key) for rec in mine), default=0.0),
                "later_at": int(group.get("later_at") or 0), "size": size, "sauce_at": 0, "handed": True,
                "trashed": int(group.get("trashed") or 0), "parts": 1, "first": "", "page": group.get("open") or "",
                "fill": dict(group.get("fill") or {}), "url": group.get("url", ""), "folder": "", "error": "",
                "site": {}, "parts_of": []}
        out.append({"key": PREFIX + gid_of(key), "name": name, "said": str(group.get("name") or ""),
                    "where": "отдельные картинки", "set": False, "loose": True, "state": state,
                    "hand": group.get("hand", ""), "works": [work], "pages": len(mine), "size": size,
                    "at": int(group.get("at") or 0), "accepted": len(accepted),
                    "placed": bool(group.get("placed")), "can_say": bool(group.get("url") or group.get("seen_url")),
                    "label": str(group.get("label") or group.get("seen_label") or ""),
                    "files": [{"name": Path(str(rec.get("path") or "")).name, "state": rec.get("state") or "",
                               "page": rec.get("page", ""), "why": rec.get("why", ""), "how": rec.get("how", ""),
                               "sim": LP.sim_of(rec, key), "chapter": LP.chapter_for(rec, key)[1],
                               "trashed": bool(rec.get("trashed"))} for rec in mine[:LISTED]]})
    return out


def waiting(settings: dict) -> int:
    """Pictures of Брак still to be asked of SauceNAO again (the block's line about them)."""
    return sum(1 for rec in STORE.read()["files"].values() if rec.get("state") == ASK)
