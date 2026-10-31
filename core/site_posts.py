"""What the sites say about the posts the library's cards came from — the parent post, who uploaded it, the post's own
source links (the table ``site_posts``): what сборки are made of (``core/card_sets.py``).

Kept when a card comes: the grabber and the subscriptions have the site's answer at hand (``rows_from_candidates``),
the parser its posts (``Post.meta``, filled by every engine from the same answer — :func:`fields`).  For the cards from
before, the sites are asked once, in the background (:func:`backfill`, a duty of ``core/background_duties.py``): the
posts in packs — ``id:1,2,3`` on Danbooru, ATF, e621 and yande.re, ``( id:1 ~ id:2 )`` on rule34.xxx, ``{id:1 ~ id:2}``
on Gelbooru.  A way of asking for a pack is trusted only once a site answered it with two or more of the asked posts
and nothing else; until then (and on a site that answers it with posts nobody asked for) the posts it could not tell
about are asked one by one.  What a site said is kept for ever: a post it does not have (deleted, hidden) is kept as
«gone» and not asked again.  A site that does not answer rests :data:`FAILED_PAUSE`; a site without its key
(rule34.xxx) or switched off by the site table is not asked.  Nothing but these questions goes to the network.

(v5.44.4) While the parser or the subscriptions use the sites the questions go on, only slower (:data:`BUSY_BUDGET` a
round): the pace of every site is shared by all who ask it (``core/http_rate_limiter``, the parser's ``_HostGate``),
and a few packs are nothing to a parser's run — which at the owner's took days, the questions waited all the time, and
the cards from before stayed without their uploader and parent: in no сборка.
"""
from __future__ import annotations

import logging
import re
import threading
import time

_LOG = logging.getLogger("local_booru.site_posts")

ENGINES = ("danbooru", "e621", "gelbooru", "moebooru")     # engines whose posts have a parent and an uploader
FAILED_PAUSE = 10 * 60          # seconds a site that did not answer is not asked again
RESCAN = 15 * 60                # seconds between two looks at which posts are still to be asked
ROUND_BUDGET = 20.0             # seconds one background round asks at most
BUSY_BUDGET = 6.0               # …while the parser or the subscriptions use the sites (v5.44.4)
ATTEMPTS = 2                    # tries of one request
SOURCE_MAX = 2000               # characters of a post's source links kept
_URL_RE = re.compile(r"https?://[^\s|<>\"']+", re.I)


# ── what a site's answer says ──────────────────────────────────────────────────────────
def _number(value) -> int:
    try:
        number = int(str(value if value is not None else "").strip() or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _sources(value) -> str:
    texts = value if isinstance(value, list) else [value]
    urls: list = []
    for text in texts:
        for url in _URL_RE.findall(str(text or "")):
            url = url.rstrip(".,;)]}")
            if url not in urls:
                urls.append(url)
    out = ""
    for url in urls:
        if len(out) + len(url) + 1 > SOURCE_MAX:
            break
        out = f"{out}\n{url}" if out else url
    return out


def _engine_of(raw: dict, engine: str) -> str:
    if engine in ENGINES:
        return engine
    if isinstance(raw.get("relationships"), dict) or isinstance(raw.get("file"), dict):
        return "e621"
    if "tag_string" in raw or "uploader_id" in raw:
        return "danbooru"
    if "owner" in raw or "hash" in raw or "change" in raw:
        return "gelbooru"
    if "author" in raw:
        return "moebooru"
    return "gelbooru"


def fields(raw, engine: str = "") -> dict:
    """{"post_id", "parent_id", "uploader", "source"} of a site's raw post ({} — not a post).  ``uploader`` is the
    uploader's number ("#123") on the engines that always give it (Danbooru, e621, Moebooru) and the name on Gelbooru
    and rule34.xxx (their JSON has only the name) — the same person is the same text on one site."""
    if not isinstance(raw, dict):
        return {}
    post_id = _number(raw.get("id"))
    if not post_id:
        return {}
    kind = _engine_of(raw, engine)
    if kind == "e621":
        rel = raw.get("relationships") if isinstance(raw.get("relationships"), dict) else {}
        parent = _number(rel.get("parent_id"))
        who = f"#{_number(raw.get('uploader_id'))}" if _number(raw.get("uploader_id")) else ""
        source = _sources(raw.get("sources") or [])
    elif kind == "danbooru":
        parent = _number(raw.get("parent_id"))
        who = f"#{_number(raw.get('uploader_id'))}" if _number(raw.get("uploader_id")) else ""
        source = _sources(raw.get("source"))
    elif kind == "moebooru":
        parent = _number(raw.get("parent_id"))
        who = (f"#{_number(raw.get('creator_id'))}" if _number(raw.get("creator_id"))
               else str(raw.get("author") or "").strip())
        source = _sources(raw.get("source"))
    else:
        parent = _number(raw.get("parent_id"))
        who = str(raw.get("owner") or "").strip() or (
            f"#{_number(raw.get('creator_id'))}" if _number(raw.get("creator_id")) else "")
        source = _sources(raw.get("source"))
    if parent == post_id:
        parent = 0
    return {"post_id": post_id, "parent_id": parent, "uploader": who[:100], "source": source}


def canonical_site(host: str) -> str:
    """The site's name in ``site_posts``: its host as the parser knows it (``api.rule34.xxx`` → ``rule34.xxx``)."""
    from core.parser.sites import HOST_ALIASES
    from core.parser.util import clean_host
    clean = clean_host(host)
    return HOST_ALIASES.get(clean, clean)


def engine_of_host(settings: dict, host: str) -> str:
    """The engine of a site of the table or of the program's catalogue ("" — unknown or not one of :data:`ENGINES`)."""
    from core.parser.sites import engine_class, table_config
    site = canonical_site(host)
    if not site:
        return ""
    cls = engine_class(table_config(settings, site) or {}, site)
    engine = getattr(cls, "engine", "") if cls is not None else ""
    return engine if engine in ENGINES else ""


def from_post(post) -> dict:
    """The row of a parser ``Post`` ({} — it carries nothing: a post of the library, of a relay)."""
    meta = getattr(post, "meta", None) or {}
    if not meta.get("post_id") or not getattr(post, "site", ""):
        return {}
    return {**meta, "site": canonical_site(post.site)}


def rows_from_candidates(settings: dict, candidates) -> list:
    """The rows of a subscription's / the grabber's candidates [(priority, site, raw post)]."""
    out, seen = [], set()
    for item in candidates or []:
        try:
            _priority, host, raw = item
        except (TypeError, ValueError):
            continue
        engine = engine_of_host(settings, str(host or ""))
        if not engine:
            continue
        row = fields(raw, engine)
        key = (canonical_site(str(host)), row.get("post_id"))
        if row and key not in seen:
            seen.add(key)
            out.append({**row, "site": key[0]})
    return out


def record(con, image_id: int, rows, *, now: int | None = None) -> int:
    """Keep what the sites said about a card's posts (inside the caller's write); the number of rows written."""
    stamp = int(now if now is not None else time.time())
    written = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        site = canonical_site(str(row.get("site") or ""))
        post_id = _number(row.get("post_id"))
        if not site or not post_id:
            continue
        con.execute(
            "INSERT OR REPLACE INTO site_posts(image_id, site, post_id, parent_id, uploader, source, state, checked_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (int(image_id), site, post_id, _number(row.get("parent_id")), str(row.get("uploader") or "")[:100],
             str(row.get("source") or "")[:SOURCE_MAX], "gone" if row.get("gone") else "ok", stamp))
        written += 1
    return written


def enabled(settings: dict) -> bool:
    """«Настройки → Галерея → Узнать у сайтов про уже скачанное»."""
    return bool((settings or {}).get("card_sets_ask_sites", True))


# ── asked of the sites, once ────────────────────────────────────────────────────────────
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()     # one round at a time
_STATE: dict = {
    "queue": {},          # host -> {post id: [(image id, source id)]} still to be asked (newest cards first)
    "scanned_at": 0.0,    # time.monotonic() of the last look
    "db": "",             # the base the queue is of
    "sites": {},          # host -> {"style", "tried", "singles", "sure", "paused_until", "note"}
    "asked": 0,           # posts asked in this run of the program
    "busy": "",           # who the last round waited for
}


def _site_state(host: str) -> dict:
    return _STATE["sites"].setdefault(host, {"style": "", "tried": [], "singles": False, "sure": [],
                                             "paused_until": 0.0, "note": ""})


def forget() -> None:
    """Forget the queue and what was learnt about the sites' ways of asking (another base; the checks)."""
    with _LOCK:
        _STATE.update(queue={}, scanned_at=0.0, db="", sites={}, asked=0, busy="")


def _scan(settings: dict) -> dict:
    """host -> {post id: [(image id, source id)]}: the live picture cards' posts on sites of :data:`ENGINES` not asked
    yet.  The source link is checked again when the answer is written: a card deleted meanwhile may have given its
    number to a new card (SQLite reuses numbers) — that one does not get another post's answer."""
    from core.database.connection import db
    from core.parser.util import post_id_from_url
    with db(settings, readonly=True) as con:
        hosts = [str(r[0] or "") for r in con.execute("SELECT DISTINCT host FROM sources")]
        wanted = {}
        for host in hosts:
            if host and engine_of_host(settings, host):
                wanted[host] = canonical_site(host)
        known = {(int(r[0]), str(r[1]), int(r[2])) for r in con.execute("SELECT image_id, site, post_id FROM site_posts")}
        queue: dict = {}
        names = sorted(wanted)
        for start in range(0, len(names), 200):
            part = names[start:start + 200]
            marks = ",".join("?" * len(part))
            rows = con.execute(
                f"SELECT x.image_id, x.source_id, s.host, s.url FROM sources s JOIN image_sources x ON x.source_id=s.id "
                f"JOIN images i ON i.id=x.image_id WHERE i.deleted=0 AND COALESCE(i.is_video,0)=0 "
                f"AND s.host IN ({marks}) ORDER BY x.image_id DESC", part).fetchall()
            for image_id, source_id, host, url in rows:
                post = post_id_from_url(str(url or ""))
                if not post.isdigit() or int(post) <= 0:
                    continue
                site = wanted[str(host)]
                if (int(image_id), site, int(post)) in known:
                    continue
                cards = queue.setdefault(site, {}).setdefault(int(post), [])
                if all(card != int(image_id) for card, _source in cards):
                    cards.append((int(image_id), int(source_id)))
    return queue


def waiting(settings: dict) -> int:
    """Posts still to be asked (as of the last look)."""
    with _LOCK:
        return sum(len(posts) for posts in (_STATE["queue"] or {}).values())


def status(settings: dict) -> dict:
    """For «Настройки»: {"known" — posts the sites told about, "gone", "waiting" — still to be asked, "notes" — what the
    sites said, "busy" — who the questions wait for}."""
    from core.database.connection import db
    try:
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT COUNT(*), COALESCE(SUM(state='gone'),0) FROM site_posts").fetchone()
        known, gone = int(row[0] or 0), int(row[1] or 0)
    except Exception:
        known, gone = 0, 0
    with _LOCK:
        notes = {host: st["note"] for host, st in _STATE["sites"].items() if st.get("note")}
        left = sum(len(posts) for posts in (_STATE["queue"] or {}).values())
        scanned = bool(_STATE["scanned_at"])
        busy = _STATE.get("busy") or ""
    return {"known": known, "gone": gone, "waiting": left, "scanned": scanned, "notes": notes, "busy": busy}


class _Client:
    """The parser's sites with fewer tries, made when the sites are asked."""

    def __init__(self, settings: dict):
        from core.parser.context import ParserContext
        from core.parser.http import HttpClient
        from core.shutdown import is_shutting_down

        class _Http(HttpClient):
            def request(self, method, url, **kwargs):
                kwargs["attempts"] = min(int(kwargs.get("attempts", 3) or 3), ATTEMPTS)
                return super().request(method, url, **kwargs)
        self.notes: list = []
        self.http = _Http(settings, log=self._note, cancel_check=is_shutting_down, pause_wait=lambda: None)
        self.ctx = ParserContext(settings, self.http, self._note, lambda *_a: None)
        self.sites: dict = {}

    def _note(self, message: str) -> None:
        from core.redaction import sanitize_text
        self.notes.append(sanitize_text(str(message)))
        del self.notes[:-20]

    def site(self, host: str):
        if host not in self.sites:
            from core.parser.sites import site_for_host
            site = site_for_host(self.ctx, host, detect=False)
            if site is not None:
                site.preflight()
            self.sites[host] = site
        return self.sites[host]


def _answer(site, ids: list, style: str) -> tuple[dict, bool]:
    """({post id: fields} of the asked posts in the site's answer, whether it held a post nobody asked for)."""
    asked = set(ids)
    found: dict = {}
    stray = False
    for raw in site.posts_by_ids([str(i) for i in ids], style) or []:
        row = fields(raw, site.engine)
        if not row:
            continue
        if row["post_id"] in asked:
            found.setdefault(row["post_id"], row)
        else:
            stray = True
    return found, stray


def _prove(site, st: dict, sure: list) -> None:
    """Find a way of asking for a pack the site understands: it must give exactly ``sure`` — posts it is known to
    have (found one by one) — and nothing else.  None does: the site is asked post by post."""
    from core.parser.sites import SiteUnavailable
    for style in [s for s in site.id_batch_styles() if s and s not in st["tried"]]:
        try:
            found, stray = _answer(site, sure, style)
        except SiteUnavailable as exc:
            if exc.transient or site.disabled_reason:
                return                                 # the site is not answering now: proved with the next post found
            st["tried"].append(style)                  # refused (HTTP 4xx): the next way
            continue
        st["tried"].append(style)
        if not stray and set(found) == set(sure):
            st["style"] = style
            return
    st["singles"] = True


def _ask(site, ids: list, st: dict) -> tuple[dict, list, list]:
    """Ask the site about ``ids``: ({post id: fields}, the ids it surely does not have, the ids to ask later).  A pack
    only in a proven way of asking; else the first post alone — two posts found so prove a way of asking for packs.
    Raises what the site's request raises (the caller pauses the site)."""
    if st["style"] and len(ids) > 1:
        found, stray = _answer(site, ids, st["style"])
        if stray:                                      # the site stopped understanding it: proved again
            st["style"] = ""
            st["sure"] = []
            return found, [], [i for i in ids if i not in found]
        return found, [i for i in ids if i not in found], []
    first = ids[0]
    found, _stray = _answer(site, [first], "")
    if found and not st["style"] and not st["singles"] and int(getattr(site, "id_batch_limit", 0) or 0) >= 2:
        st["sure"].append(first)
        if len(st["sure"]) >= 2:
            _prove(site, st, st["sure"][-2:])
    return found, ([] if found else [first]), ids[1:]


MAINTENANCE = "идёт обслуживание"      # a job of «Обслуживание» has the parser: nothing is asked meanwhile


def sites_busy() -> str:
    """Who uses the sites now — the parser, a check of the subscriptions (the grabber's downloads too), a job of
    «Обслуживание»: the questions wait for them ("" — nobody)."""
    from core import subscription_jobs
    from core.parser import jobs as parser_jobs
    if parser_jobs.is_running():
        return "работает парсер" if not parser_jobs.owner().startswith("обслуживание") else MAINTENANCE
    if subscription_jobs.is_running():
        return "проверяются подписки"
    return ""


def backfill(settings: dict, *, stop_check=None, budget: float = ROUND_BUDGET, client: _Client | None = None) -> dict:
    """One round: ask the sites about the posts not asked yet, as much as ``budget`` seconds allow; while the parser or
    the subscriptions use the sites (``sites_busy``) — :data:`BUSY_BUDGET` at most (v5.44.4: before, nothing — and a
    parser's run of days left the old cards out of сборки); nothing while «Обслуживание» has the sites.
    {"asked": posts asked, "written": rows kept, "more": something is left, "busy": who the round shared them with}."""
    from core.database.connection import writes_blocked
    if not enabled(settings) or writes_blocked():
        return {"asked": 0, "written": 0, "more": False}
    busy = sites_busy()
    with _LOCK:
        _STATE["busy"] = busy
    if busy == MAINTENANCE:
        return {"asked": 0, "written": 0, "more": True, "busy": busy}
    if busy:
        budget = min(budget, BUSY_BUDGET)
    if not _RUN_LOCK.acquire(blocking=False):            # another round is asking right now
        return {"asked": 0, "written": 0, "more": True}
    try:
        result = _round(settings, stop_check, budget, client)
    finally:
        _RUN_LOCK.release()
    return {**result, "busy": busy} if busy else result


def _base_key(settings: dict) -> str:
    """Which base the queue is of: its file and the file's identity — «Удалить всё» makes a new base at the same place,
    and its cards and sources are numbered anew (the old queue would give their answers to other cards)."""
    import os
    from core.database.connection import db_path
    path = str(db_path(settings))
    try:
        return f"{path}|{os.stat(path).st_ino}"
    except OSError:
        return path


def _round(settings: dict, stop_check, budget: float, client) -> dict:
    from core.database.connection import db
    from core.parser.http import Cancelled, HttpFailure
    from core.parser.sites import SiteUnavailable
    from core.redaction import sanitize_text
    started = time.monotonic()
    base = _base_key(settings)
    with _LOCK:
        stale = _STATE["db"] != base or not _STATE["scanned_at"] or time.monotonic() - _STATE["scanned_at"] > RESCAN
    if stale:
        queue = _scan(settings)
        with _LOCK:
            if _STATE["db"] != base:
                _STATE["sites"] = {}
            _STATE.update(queue=queue, scanned_at=time.monotonic(), db=base)
    asked = written = 0
    while time.monotonic() - started < budget:
        if stop_check is not None and stop_check():
            break
        with _LOCK:
            hosts = [h for h, posts in _STATE["queue"].items() if posts
                     and _site_state(h)["paused_until"] <= time.monotonic()]
        if not hosts:
            break
        progressed = False
        for host in hosts:
            if (stop_check is not None and stop_check()) or time.monotonic() - started >= budget:
                break
            if client is None:
                client = _Client(settings)
            try:
                site = client.site(host)
            except Exception as exc:  # a broken row of the site table: said, the other sites go on
                site = None
                _LOG.warning("site posts: %s not set up: %s", host, sanitize_text(f"{type(exc).__name__}: {exc}"))
            with _LOCK:
                st = _site_state(host)
                posts = _STATE["queue"].get(host) or {}
                if site is None or getattr(site, "engine", "") not in ENGINES:
                    st["note"] = "программа не умеет спрашивать этот сайт"
                    st["paused_until"] = time.monotonic() + RESCAN
                    continue
                if site.disabled_reason:
                    st["note"] = sanitize_text(site.disabled_reason)
                    st["paused_until"] = time.monotonic() + RESCAN
                    continue
                size = max(1, int(getattr(site, "id_batch_limit", 0) or 1)) if st["style"] else 1
                ids = list(posts)[:size]
            try:
                found, gone, again = _ask(site, ids, st)
            except (SiteUnavailable, HttpFailure, Cancelled, OSError, ValueError) as exc:
                with _LOCK:
                    st["note"] = f"не ответил: {sanitize_text(str(exc))[:200]}"
                    st["paused_until"] = time.monotonic() + FAILED_PAUSE
                _LOG.info("site posts of %s not asked: %s", host, sanitize_text(str(exc)))
                continue
            progressed = True
            now = int(time.time())
            with _LOCK:
                cards = {pid: list(posts.get(pid) or []) for pid in ids}
                for pid in list(found) + list(gone):
                    posts.pop(pid, None)
                for pid in again:                       # to the end of the queue: asked one by one later
                    if pid in posts:
                        posts[pid] = posts.pop(pid)
                st["note"] = ""
                _STATE["asked"] += len(found) + len(gone)
            rows = [(card, {**row, "site": host}) for pid, row in found.items() for card in cards.get(pid, [])]
            rows += [(card, {"site": host, "post_id": pid, "gone": True}) for pid in gone for card in cards.get(pid, [])]
            asked += len(found) + len(gone)
            if rows:
                with db(settings, write=True) as con:
                    for (image_id, source_id), row in rows:
                        if con.execute("SELECT 1 FROM image_sources WHERE image_id=? AND source_id=?",
                                       (image_id, source_id)).fetchone():
                            written += record(con, image_id, [row], now=now)
        if not progressed:
            break
    with _LOCK:
        more = any(posts for posts in _STATE["queue"].values())
    return {"asked": asked, "written": written, "more": more}
