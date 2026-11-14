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

(v5.44.170) **A page of a gallery of nhentai** lying loose is a page of a manga too (the owner, of 5.44.169's «не
сделано: отдельные картинки по базе nhentai»: «почему?»).  SauceNAO's answer names such a page by its indexes of
hentai (``manga_pictures.gallery_hits``: the gallery's names, and — hints — its number and the page's); the manga it
makes is marked ``gallery`` and is nhentai's to confirm, not the sites': ``parser_manga_nhentai.check_loose`` finds the
gallery (the one the index pointed at, then by its names — through the program's gates) and looks for the picture
among its pages.  Confirmed — the gallery is downloaded whole by the program's own downloader of nhentai
(``parser_manga_nhentai.fetch``; the same switch, the same «while nobody is at the computer»), and its pictures leave
Брак once its folder is in «Манга».  Брак's old pictures whose «источник без тегов» is an index of hentai are asked
again as the ones of the indexes of manga (:func:`_sweep`).
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
GALLERY_RULE = 4                # E-Hentai image hash links and public photo galleries
OLD_GALLERIES_A_ROUND = 32       # upgrade bookkeeping is bounded; normal rounds retain their time/search limits

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
        whole = " ".join(str(hit.get("whole") or "").split())[:300]
        others = [" ".join(str(n).split())[:300] for n in hit.get("names") or [] if str(n).strip()][:3]
        if G.title("saucenao", names=[n for n in (name, whole, *others) if n]):
            continue                                    # (closed: neither noted nor said)
        had.add(key)
        kept = {"name": name, "part": " ".join(str(hit.get("part") or "").split())[:80],
                "link": str(hit.get("link") or "")[:300], "sim": round(float(hit.get("sim") or 0), 1),
                "index": int(hit.get("index") or 0)}
        if kept["index"] in _galleries():               # (v5.44.170) a page of a gallery: what finds the gallery
            kept.update(whole=whole, names=others, gallery=int(hit.get("gallery") or 0),
                        page=int(hit.get("page") or 0))
        out.append(kept)
        if len(out) >= HITS:
            break
    return out


def _galleries() -> tuple:
    from core.manga_pictures import HENTAI_INDEXES
    return HENTAI_INDEXES


def _told(name: str, hit: dict) -> str:
    if int(hit.get("index") or 0) in _galleries():      # (v5.44.170)
        return f"«{name}» — SauceNAO: страница галереи «{hit['name']}» ({float(hit.get('sim') or 0):.0f} %)"
    _tome, number = LP.chapter_of(hit.get("part"))
    return (f"«{name}» — SauceNAO: страница манги «{hit['name']}»" + (f", глава {number}" if number else "")
            + f" ({float(hit.get('sim') or 0):.0f} %)")


def _group_for(data: dict, hit: dict) -> str:
    """Same short gallery title is not evidence of the same translation or gallery."""
    from core import manhwa_sources as S
    key = S.name_key(hit["name"])
    if int(hit.get("index") or 0) not in _galleries():
        return key
    group = data["groups"].get(key) or {}
    number = str(hit.get("gallery") or "")
    whole = S.name_key(hit.get("whole") or "")
    if group and (not group.get("gallery") or not hit_matches(hit, key, group)):
        source = _gallery_source(hit.get("link"))
        identity = number or hashlib.sha256((source or whole).encode("utf-8")).hexdigest()[:16]
        return key + ":gallery:" + identity
    return key


def _gallery_source(link) -> str:
    from core import manhwa_sources as S, parser_manga_nhentai as PN
    number = PN.number_of(link)
    if number:
        return f"{PN.SITE}:{number}"
    try:
        source, key = S.source_for(str(link or ""))
    except ValueError:
        return ""
    return f"{source.name}:{key}" if source and key else ""


def hit_matches(hit: dict, key: str, group: dict) -> bool:
    """A saved hit belongs to this row; a source number is stronger than the row's short title."""
    from core import manhwa_sources as S
    if S.name_key(hit.get("name")) != S.name_key(group.get("name") or key.split(":gallery:")[0]):
        return False
    number = str(hit.get("gallery") or "")
    expected = str(group.get("source_gallery") or (group.get("key") if group.get("site") == _nhentai() else "") or "")
    source = _gallery_source(hit.get("link"))
    previous = _gallery_source(group.get("source_link"))
    if not previous and group.get("site") == "ehentai" and group.get("key"):
        previous = "ehentai:" + str(group["key"])
    if source and previous and source != previous or number and expected and number != expected:
        return False
    if source and source == previous or number and number == expected:
        return True
    whole, before = S.name_key(hit.get("whole")), S.name_key(group.get("source_whole"))
    return not (whole and before and whole != before)


def _note(data: dict, path: str, md5: str, kept: list, media: str, now: int) -> str:
    """A picture and the manga its answer names put into ``data``; the journal's line ("" — nothing new to say)."""
    from core import manhwa_sources as S
    best = kept[0]
    key = _group_for(data, best)
    norm = MD._norm(path)
    fresh = {"name": best["name"], "at": now, "state": ""}
    if best["index"] in _galleries():                   # (v5.44.170) a gallery: nhentai confirms it, not the sites
        fresh["gallery"] = True
        fresh["gallery_rule"] = GALLERY_RULE
        fresh["source_gallery"] = best.get("gallery") or 0
        fresh["source_whole"] = best.get("whole") or ""
        fresh["source_link"] = best.get("link") or ""
    group = data["groups"].setdefault(key, fresh)
    from core import parser_gallery_sources as PG
    new_source = bool(PG.source_link(best.get("link")) and not PG.source_link(group.get("source_link"))
                      and not group.get("site"))
    if new_source:
        group["source_link"] = best["link"]
        if group.get("state") == NOT and not group.get("hand"):
            group.update(state="", later_at=0)
    rec = dict(data["files"].get(norm) or {})
    known = not new_source and rec.get("state") in (MANGA, NOT) and rec.get("group") == key
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
    gallery = bool(group.get("gallery"))
    if group.get("state") == MANGA:
        title = group.get("title") or group["name"]
        if (not gallery and best["sim"] >= PM.SURE) or group.get("hand") == MANGA:
            rec.update(state=MANGA, how="sure" if best["sim"] >= PM.SURE else "hand")
            return PM.line("МАНГА", f"{told} — " + ("галерея" if gallery else "манга")
                           + f" «{title}» уже подтверждена: картинка уйдёт из «Брака»")
        return PM.line("ЖДЁТ", f"{told} — " + ("галерея" if gallery else "манга") + f" «{title}» уже подтверждена; "
                       "эту картинку сверю со страницами " + ("галереи" if gallery else "главы"))
    if group.get("state") == NOT:
        group["state"] = ""                             # one more picture of it: compared with the title's pages too
    if gallery:
        return PM.line("ЖДЁТ", f"{told} — сверю со страницами галереи на nhentai: совпадёт — галерея в «Мангу», "
                               "картинка уйдёт из «Брака»")
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
    """The title's download waits in the queue of «Загрузки» or goes now ((v5.44.170) a gallery's — in the program's
    download of nhentai)."""
    from core import manhwa_queue as Q, parser_manga_nhentai as PN
    number = PN.number_of(url)
    if number:
        return PN.going(number)
    current = Q.current_record() or {}
    return bool(Q.queued(url, "")) or current.get("url") == url


def _open_link(settings: dict, site: str, tkey: str) -> str:
    """The page of the title in the library ("" — not found by its link's site)."""
    from core import manhwa_download as D, manhwa_queue as Q, parser_manga_nhentai as PN
    if site == PN.SITE:                                 # (v5.44.170) a gallery: its folder of the manga folder
        return PN.page_link(settings, int(tkey)) if str(tkey).isdigit() else ""
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


def _choice_of(group: dict) -> tuple:
    """The decision a background operation started with; a newer user choice invalidates its answer."""
    return tuple(group.get(name) for name in ("hand", "pending_link", "again", "state", "url", "site", "key",
                                               "source_link", "eh_photo"))


def _picture_revision(rec: dict) -> tuple:
    """A picture's evidence when it was selected for comparison (a later parser result must be checked anew)."""
    return tuple(rec.get(name) for name in ("state", "hits", "group", "path", "md5", "media"))


def _place(settings: dict, key: str, say, *, asked: bool = False) -> dict:
    """A confirmed manga put into «Манга»: its download begun (once nobody is at the computer; never twice at a
    time; ``asked`` — «Скачать сейчас», the person's own word: now, and whatever was tried before), and — when the
    title is in the library — the cards of its pictures out of Брак (the trash, «перенесено в мангу»).
    {"queued", "trashed", "fill" — what its download is doing now}."""
    from core import catalog_have, manhwa_download as D, manhwa_queue as Q
    out = {"queued": 0, "trashed": 0}
    data = STORE.read()
    group = dict(data["groups"].get(key) or {})
    if group.get("pending_link") or group.get("state") != MANGA or not group.get("url"):
        return out
    mine = [(norm, rec) for norm, rec in data["files"].items()
            if rec.get("group") == key and rec.get("state") == MANGA and not rec.get("done")]
    title = str(group.get("title") or group.get("name") or "")
    url, site, tkey = str(group["url"]), str(group.get("site") or ""), str(group.get("key") or "")
    now = int(time.time())
    values: dict = {}
    photo = bool(group.get("eh_photo")) and site == "ehentai"
    # (v5.44.171) a gallery is «there» by its own number alone — not by a name: a manga of the library that is merely
    # called so (another work — «Summer»; the same one in another language) is not this gallery, and its pictures
    # would leave Брак «перенесено в мангу» with nothing of theirs in «Манга»
    # A name can belong to another work, scan or translation on every site. Only the confirmed source identity
    # proves that the download exists before the source pictures are moved out of Брак.
    # The feed also remembers possible helper sites. Merely discovering one does not download its translation.
    if photo:
        from core import parser_ehentai_photos
        there = bool(parser_ehentai_photos.complete(settings, url))
    else:
        have = catalog_have.downloaded("manga", settings)
        there = bool(site and tkey and (site, tkey) in have.primary_keys)
    if not photo and not there and site and tkey and site != _nhentai():
        # A source was just attached to an existing folder: the catalogue's brief cache may not see it yet.
        linked = D.linked_folder(settings, site, tkey)
        there = bool(linked and Path(linked[1]).is_dir())
    done: dict = {}                     # a picture nothing more is to be done with -> whether its card went to the trash
    still: set = set()                  # the ones whose card is in Брак after all (the person's own сборка, no backup)
    if there:
        gone = 0
        if photo:
            done = {norm: False for norm, _rec in mine}
        elif mine:
            paths = [str(rec.get("path") or "") for _n, rec in mine]
            was = _in_brak(settings, paths)
            if was:
                with STORE._lock:
                    current = STORE.read()
                    if _choice_of(current["groups"].get(key) or {}) != _choice_of(group):
                        return out
                    # A new parser result may move a picture to another group without changing this group.
                    if any(_picture_revision(current["files"].get(norm) or {}) != _picture_revision(rec)
                           or (current["files"].get(norm) or {}).get("done") for norm, rec in mine):
                        return out
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
    elif site == _nhentai():                            # (v5.44.170) a gallery: the program's download of nhentai
        from core import parser_manga_nhentai as PN
        with STORE._lock:
            if _choice_of(STORE.read()["groups"].get(key) or {}) != _choice_of(group):
                return out
            queued = PN.fetch(settings, int(tkey)).get("ok")
        if queued:
            values.update(queued_at=now, queue_tries=int(group.get("queue_tries") or 0) + 1,
                          fill={"state": "queued"})
            out["queued"] = 1
            say(PM.line("МАНГА", f"«{title}» — скачиваю галерею в «Мангу» (видно в «Загрузках»); её картинки уйдут "
                                 "из «Брака», когда она появится в «Манге»"))
        else:                                           # (its downloader is busy, no manga folder: the next round
            values["fill"] = {"state": "soon"}          # — the row says «Сейчас скачаю», not «жду, пока вы отойдёте»)
    else:
        record = {"url": url, "title": title, "branch": "", "where": "manga" if photo else "",
                  "follow": not photo, "helpers": not photo,
                  "chapters": None, "hentai": bool(group.get("adult"))}

        def work(say_, stop_, record=record):
            return Q.work(record, settings, say_, stop_)
        with STORE._lock:
            if _choice_of(STORE.read()["groups"].get(key) or {}) != _choice_of(group):
                return out
            Q.start(f"Скачать: {title}", work, record=record, about=PREFIX + gid_of(key), title=title, count=0)
        values.update(queued_at=now, queue_tries=int(group.get("queue_tries") or 0) + 1, fill={"state": "queued"})
        out["queued"] = 1
        say(PM.line("МАНГА", f"«{title}» — скачиваю в «Мангу» (видно в «Загрузках»)" +
                    ("" if photo else "; её картинки уйдут из «Брака», когда тайтл появится в «Манге»")))
    if values and any(group.get(k) != v for k, v in values.items()) or mine and there:
        def put(found: dict) -> None:
            kept = found["groups"].get(key)
            if kept is None or _choice_of(kept) != _choice_of(group):
                return
            kept.update(values)
            if there:
                for norm, rec in mine:
                    current = found["files"].get(norm)
                    if current is None or _picture_revision(current) != _picture_revision(rec) or current.get("done"):
                        continue
                    if norm in done:
                        found["files"][norm].update(done=1, trashed=1 if done[norm] else 0)
                    elif norm in still:
                        found["files"][norm]["tries"] = int(found["files"][norm].get("tries") or 0) + 1
        STORE.change(put)
    out["fill"] = str((values.get("fill") or group.get("fill") or {}).get("state") or "")
    return out


def _nhentai() -> str:
    from core import parser_manga_nhentai as PN
    return PN.SITE


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
                "WHERE +active=1 AND reason='source_only' AND (source_label LIKE 'SauceNAO Index #3%' "
                "OR source_label LIKE 'SauceNAO Index #18%')")]        # ((v5.44.170) #18, #38: the galleries)
    except Exception as exc:  # noqa: BLE001 - the base busy (or older than the columns): looked through next time
        _LOG.info("parser loose: Брак not looked through: %s", type(exc).__name__)
        return 0
    wanted = []
    for path, media, label, sim in rows:
        index = _INDEX.search(label)
        if index and int(index.group(1)) in (*P.INDEXES, *P.HENTAI_INDEXES) and sim >= P.SIMILAR and path \
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


def mark_old_galleries(limit: int = OLD_GALLERIES_A_ROUND) -> int:
    """Once per gallery rule, queue old automatic negatives with their saved SauceNAO evidence.

    The cursor advances across at most ``limit`` rows per call. Manual choices, pending links, placed
    pictures and confirmed galleries remain untouched. No request is made here and no quota is reset.
    """
    from core import parser_manga_nhentai as PN
    progress = STORE.read().get("gallery_upgrade") or {}
    if int(progress.get("rule") or 0) >= GALLERY_RULE and progress.get("done"):
        return 0
    now = int(time.time())
    limit = min(OLD_GALLERIES_A_ROUND, max(1, int(limit)))

    def put(data: dict) -> int:
        saved = data.get("gallery_upgrade") or {}
        if int(saved.get("rule") or 0) >= GALLERY_RULE and saved.get("done"):
            return 0
        after = str(saved.get("after") or "") if int(saved.get("rule") or 0) == GALLERY_RULE else ""
        remaining = sorted(key for key in data["groups"] if key > after)
        selected = remaining[:limit]
        candidates = {}
        for key in selected:
            group = data["groups"][key]
            if not isinstance(group, dict) or int(group.get("gallery_rule") or 0) >= GALLERY_RULE:
                continue
            if group.get("state") != NOT or not PN.is_gallery(group):
                continue
            if any(group.get(field) for field in ("hand", "placed", "trashed", "pending_link", "again")) \
                    or group.get("how") in ("hand", "forbidden"):
                continue
            candidates[key] = []
        for rec in data["files"].values():
            key = rec.get("group")
            if key in candidates and rec.get("state") in ("", NOT) and not any(
                    rec.get(field) for field in ("done", "trashed", "hand")) and rec.get("how") != "hand":
                candidates[key].append(rec)
        count = 0
        for key, records in candidates.items():
            if not records:
                continue
            data["groups"][key].update(state="", how="", by="", later_at=0, searched_at=0,
                                       eh_searched_at=0, files_at=0, tries=0, again=now, gallery_rule=GALLERY_RULE)
            for rec in records:
                rec.update(state="", why="", how="")
                rec.pop("page", None)
            count += 1
        data["gallery_upgrade"] = {"rule": GALLERY_RULE, "after": selected[-1] if selected else after,
                                   "done": len(remaining) <= limit}
        return count
    return int(STORE.change(put) or 0)


def _due(data: dict, now: float) -> list:
    """The manga to look at now, the cheapest first: the ones confirmed with pictures not compared yet (no search),
    then the new ones and the ones whose wait is over — the ones SauceNAO is surest of first."""
    waiting: dict = {}
    for rec in data["files"].values():
        if rec.get("state") == "" and rec.get("group"):
            waiting[rec["group"]] = max(waiting.get(rec["group"], 0.0), LP.sim_of(rec, rec["group"]))
    out = []
    for key, group in data["groups"].items():
        if group.get("pending_link") and float(group.get("later_at") or 0) <= now:
            out.append((False, -101.0, int(group.get("at") or 0), key))
            continue
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
    if group.get("pending_link"):
        from core import parser_loose_link
        return parser_loose_link.settle(settings, asker, key, group, tell)
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
        negative = values.get("state") == NOT
        def put(found: dict) -> bool:
            nonlocal more
            kept = found["groups"].get(key)
            if kept is None or _choice_of(kept) != _choice_of(group):
                return False
            unchanged = {norm for norm, rec in data["files"].items()
                         if norm in found["files"] and _picture_revision(found["files"][norm]) == _picture_revision(rec)}
            if any(norm not in unchanged for norm, _rec in mine[:FILES]):
                return False                           # the pixels/evidence just compared are no longer current
            new_waiting = any(norm not in unchanged and rec.get("group") == key and rec.get("state") == ""
                              for norm, rec in found["files"].items())
            more = more or new_waiting
            if negative and new_waiting:
                values["state"] = ""                   # a newly arrived picture may identify what the old ones could not
            if values:
                kept.update(values)
                if values.get("state") in (MANGA, NOT):  # (v5.44.166) looked at: «Перепроверить» is done with
                    kept.pop("again", None)
            for norm, new in files.items():
                if norm in unchanged:
                    found["files"][norm].update(new)
            if negative:
                for norm, rec in found["files"].items():
                    if norm in unchanged and rec.get("group") == key and rec.get("state") == "":
                        rec.update(state=NOT, why=str(values.get("by") or ""))
            return True
        if not STORE.change(put):
            return {"state": "", "more": True, "wait": 0.0}
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
        mark_old_galleries()
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
    from core import parser_manga_nhentai as PN
    url = str(group.get("url") or group.get("seen_url") or "")
    number = PN.number_of(url)
    source, tkey = (None, str(number)) if number else S.source_for(url)
    if source is None and not number:
        return {"ok": False, "error": ("На nhentai галереи с таким названием не нашлось — скачать нечего"
                                       if group.get("gallery") else
                                       "На сайтах манги программы тайтла с таким названием не нашлось — скачать нечего")}
    site, label, adult = ((PN.SITE, PN.LABEL, True) if number else
                          (source.name, source.label, bool(source.adult_only)))

    def put(found: dict) -> None:
        kept = found["groups"].get(key)
        if kept is None:
            return
        kept.update(hand=MANGA, state=MANGA, how="hand", by="вы сказали: «Это манга»", url=url, site=site,
                    key=tkey, title=kept.get("title") or kept.get("seen_title") or kept.get("name"),
                    label=kept.get("label") or kept.get("seen_label") or label,
                    adult=bool(kept.get("adult") or kept.get("seen_adult") or adult), later_at=0)
        kept.pop("again", None)                         # (v5.44.166) his word: no second look
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
        kept.pop("pending_link", None)
        kept.pop("link_previous", None)
        kept.pop("again", None)                         # (v5.44.166) his word: no second look
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


def _gallery_label(group: dict, records: list) -> str:
    from core import parser_gallery_sources as PG
    if group.get("site") == _nhentai():
        return ""
    if group.get("site") == "ehentai" or PG.source_link(group.get("source_link")) or PG.public_link(group.get("seen_url")):
        return "E-Hentai"
    if not group.get("site") and any(hit.get("index") == 38 for rec in records for hit in rec.get("hits") or []):
        return "E-Hentai"
    return ""


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
        # (v5.44.166) «Перепроверить» pressed and not looked at yet (``parser_manga_again._loose_mark``)
        again = bool(group.get("again")) and state in ("check", WAIT) and not group.get("hand")
        work = {"key": key, "name": name, "state": "" if state == "check" else state, "how": group.get("how", ""),
                "eh_photo": bool(group.get("eh_photo")),
                "again": again, "again_at": int(group.get("later_at") or 0) if again and state == WAIT else 0,
                "again_why": str(group.get("by") or "") if again and state == WAIT else "", "again_said": {},
                "by": group.get("by", ""), "sim": max((LP.sim_of(rec, key) for rec in mine), default=0.0),
                "later_at": int(group.get("later_at") or 0), "size": size, "sauce_at": 0, "handed": True,
                "trashed": int(group.get("trashed") or 0), "parts": 1, "first": "", "page": group.get("open") or "",
                "fill": dict(group.get("fill") or {}), "url": group.get("url", ""), "folder": "", "error": "",
                "site": {}, "parts_of": []}
        out.append({"key": PREFIX + gid_of(key), "name": name, "said": str(group.get("name") or ""),
                    "gallery": bool(group.get("gallery")) or str(group.get("site") or "") == _nhentai(),
                    "gallery_label": _gallery_label(group, mine),
                    "where": "отдельные картинки", "set": False, "loose": True, "state": state, "again": again,
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
