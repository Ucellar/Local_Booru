"""The parts of a сборка the library does not have (v5.30; «серия» and «одна работа» v5.32).

A сборка (``core/card_sets.py``) is made of the library's own cards: two pages of a comic of four were downloaded, and
the box over the picture shows two.  The site has the rest.  It is asked with the grabber's own search
(``core/grabber/search.GrabberSearch``: the grabber's blocklist and «Скрыть» hold here too) the way the reference client asks rule34
(its ``sets.js``; the rules of what belongs — ``core/set_seek.py``):

- a parent post and its children (kind «parent»): ``parent:P`` for the children, ``id:P`` for the parent itself;
- a series or a work (kinds «series», «work»): the posts of the сборка's uploader around its posts on the site —
  between its first and last post, after, before (``user:ник id:>N sort:id:asc`` / ``id:<N``), each taken by the
  rules of the сборки; the pages of a comic uploaded by others (the artist's posts with the comic's tag around);
- every kind: the same work by its source — ``source:*номер*`` of the cards' links (a pixiv work, a tweet, a Patreon
  post, a Discord series…): the posts whose link names the same work, and their families on the site.

What was found is kept :data:`FAMILY_TTL` (only the parts: the rest of the answers is forgotten at once).  Its posts
whose file the library has not (``grabber.cards.mark_owned``) are shown in the сборка with «api»
(``webui/pages/post_sets.py``): their pictures come through the local server (``core/grabber/media``), only from the
addresses the site gave; «Скачать» hands the post to the program's browser import (``core/browser_import``: the
original by the post's API, the file's MD5 known, the tags of every site by it — as a download from the browser
extension).

Only a site of the parser's table that searches by tags is asked (``grabber.search.searchable_sites``).  The
uploader's posts — where a search can name the uploader: rule34.xxx, Gelbooru and the like, Moebooru (a name), e621
(``user:!N``); Danbooru tells only the uploader's number and searches by a name.

(v5.44.152) The card's page is shown already and waits for this answer, every card's page asks, and the parser may be
holding the same site: the question is short.  One try of a request, :data:`ASK_TIMEOUT`; no request more is sent once
the question has gone on for :data:`ASK_MOST` (the site's turn in the program's own throttle counts: a pause the parser
got from the site may be minutes long); a site that did not answer the first question is not asked the second.  Such a
site is remembered (:data:`SILENT_FOR`): the families and parts of other cards on it are answered from memory, in
words, without a request — and what it did not answer is asked again when that time is over, not kept as an answer.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict

_LOG = logging.getLogger("local_booru.set_remote")

FAMILY_TTL = 30 * 60            # seconds what was asked of a site is kept (its posts, what the library has of them)
KEEP = 48                       # families and сборки kept at once (v5.44.1: the families of lone cards too)
PAGES = 3                       # pages of ``parent:P`` asked (the grabber's ``per_site`` each)
PER_PAGE = 100
SHOWN_MAX = 60                  # missing parts shown at most (a parent with hundreds of children: the first ones)
ASK_TIMEOUT = (6.0, 20.0)       # seconds a request of this question waits for the site: to connect, for the answer
ASK_ATTEMPTS = 1                # tries of a request (asked again later instead: the page is shown already)
ASK_MOST = 25.0                 # seconds the whole question may go on before no request more is sent
SILENT_FOR = 180                # seconds a site that did not answer is not asked again (answered from memory)
_KEY_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,98}")
_LOCK = threading.Lock()
_FAMILIES: OrderedDict = OrderedDict()           # key -> Family (``site:parent``) or SetParts (``set-N``)
_SILENT: dict = {}                               # site -> (time.time() it is asked again, what happened — words)


class FamilyError(Exception):
    """The family cannot be asked (a site the grabber does not search, a wrong address): said in words."""


def family_key(site: str, parent: int) -> str:
    return f"{site}:{int(parent)}"


def parse_key(key: str) -> tuple[str, int] | None:
    """(site, parent post) of an address's ``f=…``, or None."""
    site, _sep, number = str(key or "").rpartition(":")
    if not _KEY_RE.fullmatch(site) or not number.isdigit():
        return None
    parent = int(number)
    return (site, parent) if 0 < parent < 10 ** 12 else None


def post_id_of(card: dict, site: str = "") -> int:
    """The card's post number on ``site`` (its first site's when not given)."""
    for item in card.get("entries") or []:
        if not site or item.get("site") == site:
            try:
                return int(item.get("post_id") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


class _Patience:
    """How long one question to a site may go on (:data:`ASK_MOST`): ``over()`` — no request more is sent, the one
    waiting for its turn at the site gives up.  ``rest()`` — the question is answered: the searches kept for their
    parts (their pictures, a download) are held by nothing."""

    def __init__(self):
        self.until: float | None = time.monotonic() + ASK_MOST

    def over(self) -> bool:
        return self.until is not None and time.monotonic() > self.until

    def rest(self) -> None:
        self.until = None


def _search(settings: dict, query: str, site: str, per_site: int, patience: _Patience):
    """The grabber's search of one site, asked the short way (v5.44.152)."""
    from core.grabber.search import GrabberSearch
    search = GrabberSearch(settings, query, [site], per_site=per_site)
    search.http.hurry(ASK_TIMEOUT, ASK_ATTEMPTS, patience.over)
    return search


def _unheard(search) -> bool:
    """The site did not answer this search (the network, its limit, a server error, not in time) — not «nothing
    found», and not a refusal for good (a key, a check for robots: said at once, nothing waits for those)."""
    return any(lane.get("failures") for lane in search.lanes.values())


def _span(seconds: float) -> str:
    seconds = max(1, int(round(seconds)))
    return f"{-(-seconds // 60)} мин" if seconds >= 60 else f"{seconds} с"


def _quiet(site: str, now: float) -> str:
    """Words when ``site`` did not answer a short while ago and is not asked yet ("" — it is asked).  Under _LOCK."""
    held = _SILENT.get(site)
    if held is None:
        return ""
    if now >= held[0]:
        del _SILENT[site]
        return ""
    return f"{site} не ответил ({held[1]}) — о недостающих частях спрошу снова через {_span(held[0] - now)}"


def _heard(found) -> None:
    """After a question: a site that answered nothing is not asked for :data:`SILENT_FOR`; one that answered is."""
    site = getattr(found, "site", "")
    if not site:
        return
    with _LOCK:
        if found.unheard and not found.cards:
            _SILENT[site] = (found.asked + SILENT_FOR, found.error or "нет ответа")
        elif not found.error:
            _SILENT.pop(site, None)


def _forget_old(now: float) -> None:
    """What was asked long ago is asked again; what a site did not answer whole (``unheard``: nothing, or a part) —
    as soon as the site is asked again.  Under _LOCK."""
    for key in [k for k, f in _FAMILIES.items()
                if now - f.asked > (SILENT_FOR if getattr(f, "unheard", False) else FAMILY_TTL)]:
        _FAMILIES.pop(key).close()


def silent_sites() -> dict:
    """{site: seconds until it is asked again} — the sites that did not answer a short while ago."""
    now = time.time()
    with _LOCK:
        return {site: until - now for site, (until, _words) in _SILENT.items() if until > now}


class Family:
    """A parent post and its children on one site, as the grabber's searches found them.  ``children_only`` — the
    parent is the library's own card (a lone card asking whether its post has children, v5.44.1): ``id:P`` is not
    asked.  ``patience`` — of the сборка's question this family is a part of (its own otherwise)."""

    def __init__(self, settings: dict, site: str, parent: int, *, children_only: bool = False,
                 patience: _Patience | None = None):
        self.site, self.parent = site, int(parent)
        self.key = family_key(site, parent)
        self.asked = time.time()
        self.error = ""
        self.unheard = False                  # the site left a question unanswered (asked again soon: SILENT_FOR)
        self.children_only = bool(children_only)
        self._own_patience = patience is None
        self.patience = patience or _Patience()
        self.searches = [_search(settings, f"parent:{self.parent}", site, PER_PAGE, self.patience)]
        if not self.children_only:
            self.searches.append(_search(settings, f"id:{self.parent}", site, 5, self.patience))
        self.cards: list = []                 # the family: the parent first, then by post number
        self._found: dict = {}                # card key -> (search, card)
        self._loaded = False
        self._lock = threading.Lock()

    def load(self) -> None:
        """Ask the site (once): every page of the children, the parent itself.  A site that did not answer about the
        children is not asked about the parent (v5.44.152: the page waits for both)."""
        with self._lock:
            if self._loaded:
                return
            errors = []
            unheard = False
            try:
                for search in self.searches:
                    for _page in range(PAGES):
                        if not search.has_more:
                            break
                        before = sum(lane["posts"] for lane in search.lanes.values())
                        search.more()
                        if sum(lane["posts"] for lane in search.lanes.values()) - before < search.per_site:
                            break                     # a page not full: the family is all there (no request more)
                    errors += [lane["error"] for lane in search.lanes.values() if lane.get("error")]
                    for card in search.cards:
                        self._found.setdefault(card["key"], (search, card))
                    if _unheard(search):
                        unheard = True
                        break
            finally:
                if self._own_patience:
                    self.patience.rest()
            self.cards = sorted((card for _s, card in self._found.values()), key=self.rank)
            if not self.cards and errors:
                self.error = errors[0]
            self.unheard = unheard
            self._loaded = True

    def rank(self, card: dict) -> tuple:
        """Its place in the family: the parent first, then by the post's number."""
        number = post_id_of(card, self.site)
        return (number != self.parent, number)

    def missing(self) -> list:
        """The family's posts whose file the library has not (looked again: a download may have come meanwhile);
        a post whose file is in the trash is missing too."""
        from core.grabber.cards import OWNED_FILE, OWNED_POST
        self.load()
        for search in self.searches:
            search.refresh_owned()
        return [card for card in self.cards if card.get("owned") not in (OWNED_FILE, OWNED_POST)]

    def find(self, key: str) -> tuple:
        """(search, card) of the card ``key`` of the family, or (None, None)."""
        self.load()
        return self._found.get(str(key or ""), (None, None))

    def close(self) -> None:
        for search in self.searches:
            search.close()


def family(settings: dict, site: str, parent: int, *, children_only: bool = False) -> Family:
    """The family of ``parent`` on ``site`` — asked now or kept from a while ago (:data:`FAMILY_TTL`; one asked for
    the children only is asked again whole when the whole is wanted)."""
    from core.grabber.search import searchable_sites
    if site not in {host for host, _label in searchable_sites(settings)}:
        raise FamilyError(f"{site} нет среди сайтов «Галереи (booru)» или он выключен — недостающие части не "
                          "спрашиваются")
    key = family_key(site, parent)
    now = time.time()
    with _LOCK:
        _forget_old(now)
        found = _FAMILIES.get(key)
        if found is not None and getattr(found, "children_only", False) and not children_only:
            _FAMILIES.pop(key).close()
            found = None
        if found is None:
            quiet = _quiet(site, now)
            if quiet:
                raise FamilyError(quiet)
            found = _FAMILIES[key] = Family(settings, site, parent, children_only=children_only)
            while len(_FAMILIES) > KEEP:
                _old_key, old = _FAMILIES.popitem(last=False)
                old.close()
        else:
            _FAMILIES.move_to_end(key)
    found.load()
    _heard(found)
    return found


def forget_all() -> None:
    """Every family and сборка asked again next time, every site too (the checks; a change of the site table)."""
    with _LOCK:
        while _FAMILIES:
            _key, old = _FAMILIES.popitem()
            old.close()
        _SILENT.clear()


# ── a сборка's parts: its family, the uploader's posts around, the same work (v5.32) ────────
INSIDE_PAGES = 5                # pages of the uploader's posts between the сборка's first and last post on the site
ARTIST_INSIDE_PAGES = 2         # …of the comic artist's posts there
WORK_FAMILIES = 2               # families of the posts found by the source asked at most
_PARTS_RE = re.compile(r"set-(\d{1,12})")
_ASC = {"gelbooru": "sort:id:asc", "danbooru": "order:id_asc", "e621": "order:id_asc", "moebooru": "order:id"}
_NO_VIDEO = {"gelbooru": "-video"}          # rule34.xxx and Gelbooru tag their videos so: pages of pictures only


def parts_key(set_id: int) -> str:
    return f"set-{int(set_id)}"


def parse_parts_key(key: str) -> int | None:
    """The сборка of an address's ``f=set-N``, or None."""
    m = _PARTS_RE.fullmatch(str(key or ""))
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def uploader_term(engine: str, owner: str) -> str:
    """How the site's search names the uploader ("" — it cannot): the name on rule34.xxx, Gelbooru and the like and on
    Moebooru, ``user:!N`` of e621's number; Danbooru's posts tell only the uploader's number, its search takes a name."""
    owner = str(owner or "").strip()
    if not owner or any(ch.isspace() for ch in owner):
        return ""
    if owner.startswith("#"):
        number = owner[1:]
        return f"user:!{number}" if engine == "e621" and number.isdigit() else ""
    return f"user:{owner}" if engine in ("gelbooru", "moebooru", "e621") else ""


def _query(*parts) -> str:
    return " ".join(str(p) for p in parts if p)


def plan(settings: dict, data: dict) -> dict:
    """What a сборка's parts are asked by (``data`` — ``card_sets.set_input``): its site — the one most of its cards'
    posts are on, of the sites the grabber searches (a family's — the family's); the uploader most of them there
    have and the range of his posts; the family's parent (kind «parent»)."""
    from core.grabber.search import searchable_sites
    from core.site_posts import engine_of_host
    order = [host for host, _label in searchable_sites(settings)]
    count: dict = {}
    for post in data["posts"]:
        if post.site in order:
            count[post.site] = count.get(post.site, 0) + 1
    site = min(count, key=lambda s: (-count[s], order.index(s))) if count else ""
    root: tuple = ()
    if data["kind"] == "parent":
        parents: dict = {}
        for post in data["posts"]:
            if post.parent:
                parents[(post.site, post.parent)] = parents.get((post.site, post.parent), 0) + 1
        if parents:
            root = max(parents, key=lambda k: (parents[k], k))
            if root[0] in order:
                site = root[0]
    owners: dict = {}
    for post in data["posts"]:
        if post.site == site and post.owner:
            owners[post.owner] = owners.get(post.owner, 0) + 1
    owner = min(owners, key=lambda o: (-owners[o], o)) if owners else ""
    numbers = [p.id for p in data["posts"] if p.site == site and owner and p.owner == owner]
    lo, hi = (min(numbers), max(numbers)) if numbers else (0, 0)
    return {"id": data["id"], "kind": data["kind"], "site": site, "engine": engine_of_host(settings, site) if site else "",
            "owner": owner, "lo": lo, "hi": hi, "root": root, "data": data,
            "signature": (data["kind"], site, owner, lo, hi, root)}


class _Lane:
    """One query on the сборка's site, page after page: the site's pictures (``posts`` — (post, card) in the order the
    site gave them)."""

    def __init__(self, settings: dict, site: str, engine: str, query: str, patience: _Patience):
        self.site, self.engine, self.query = site, engine, query
        self.search = _search(settings, query, site, PER_PAGE, patience)
        self.posts: list = []
        self.more_left = bool(self.search.lanes)

    def page(self) -> "_Lane":
        """The next page (nothing when the site has no more: a page not full was the last)."""
        from core.set_seek import site_post
        if not self.more_left or not self.search.has_more:
            self.more_left = False
            return self
        lane = next(iter(self.search.lanes.values()))
        before_posts, before = lane["posts"], len(self.search.cards)
        self.search.more()
        self.more_left = self.search.has_more and lane["posts"] - before_posts >= self.search.per_site
        for card in self.search.cards[before:]:
            for entry in card["entries"]:
                if entry.get("site") != self.site or entry.get("video"):
                    continue
                post = site_post(entry, self.engine)
                if post is not None:
                    self.posts.append((post, card))
        return self

    def pages(self, count: int) -> "_Lane":
        for _ in range(count):
            if not self.more_left:
                break
            self.page()
        return self

    def walk(self, more):
        """The posts nearest first; when they run out, another page while ``more()`` allows (the reference client's ``seqFrom``)."""
        at, extra = 0, 0
        while True:
            while at < len(self.posts):
                yield self.posts[at][0]
                at += 1
            if not self.more_left or extra >= more():
                return
            extra += 1
            self.page()

    @property
    def error(self) -> str:
        return next((lane["error"] for lane in self.search.lanes.values() if lane.get("error")), "")

    @property
    def unheard(self) -> bool:
        return _unheard(self.search)

    def close(self) -> None:
        self.search.close()


class SetParts:
    """The parts of one сборка the library has not, as its site's searches found them (``key`` — ``set-N``): the
    family (kind «parent»), the uploader's posts around and the pages of a comic by others («series», «work»), the
    same work by its source (every kind)."""

    def __init__(self, settings: dict, asked_plan: dict):
        self.settings = dict(settings)
        self.plan = asked_plan
        self.set_id = int(asked_plan["id"])
        self.key = parts_key(self.set_id)
        self.kind, self.site, self.engine = asked_plan["kind"], asked_plan["site"], asked_plan["engine"]
        self.parent = int(asked_plan["root"][1]) if asked_plan.get("root") and asked_plan["root"][0] == self.site else 0
        self.signature = asked_plan["signature"]
        self.asked = time.time()
        self.error = ""
        self.unheard = False                  # the site left a question unanswered (asked again soon: SILENT_FOR)
        self.patience = _Patience()
        self.notes: list = []                 # what was not asked, and why (words)
        self.cards: list = []                 # the parts found: the parent first, then by the post's number
        self._found: dict = {}                # card key -> (search, card)
        self._lanes: list = []                # the searches kept (only their parts)
        self._families: list = []
        self._loaded = False
        self._lock = threading.Lock()

    # ── what the page asks ───────────────────────────────────────────────────
    def load(self) -> None:
        """Ask the site (once).  A site that answers nonsense does not take the card's page down: said in ``error``."""
        from core.redaction import sanitize_text
        with self._lock:
            if self._loaded:
                return
            try:
                self._load()
            except Exception as exc:
                _LOG.warning("parts of set %s: %s", self.set_id, exc, exc_info=True)
                self.error = sanitize_text(f"{type(exc).__name__}: {exc}")[:300]
            finally:
                self.patience.rest()
                self._loaded = True

    def rank(self, card: dict) -> tuple:
        """Its place among the parts: the family's parent first, then by the post's number."""
        number = post_id_of(card, self.site)
        return (number != self.parent, number)

    def missing(self) -> list:
        """The parts whose file the library has not (looked again: a download may have come meanwhile); a post whose
        file is in the trash is missing too."""
        from core.grabber.cards import OWNED_FILE, OWNED_POST
        self.load()
        for search in self._searches():
            search.refresh_owned()
        return [card for card in self.cards if card.get("owned") not in (OWNED_FILE, OWNED_POST)]

    def find(self, key: str) -> tuple:
        """(search, card) of the part ``key``, or (None, None)."""
        self.load()
        return self._found.get(str(key or ""), (None, None))

    def close(self) -> None:
        for search in self._searches():
            search.close()

    def _searches(self) -> list:
        return [lane.search for lane in self._lanes] + [s for fam in self._families for s in fam.searches]

    # ── asking ───────────────────────────────────────────────────────────────
    def _lane(self, query: str) -> _Lane:
        return _Lane(self.settings, self.site, self.engine, query, self.patience)

    def _family(self, parent: int) -> Family:
        return Family(self.settings, self.site, parent, patience=self.patience)

    def _load(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from core import set_seek
        data = self.plan["data"]
        if not self.site:
            self.error = "у карточек сборки нет постов на сайтах, где ищет «Галерея (booru)», — спрашивать нечего"
            return
        own = {p.id: p for p in data["posts"] if p.site == self.site}       # the сборка's own posts on the site
        asc, no_video = _ASC.get(self.engine, ""), _NO_VIDEO.get(self.engine, "")
        walk_lanes: dict = {}
        if self.kind in ("series", "work") and self.plan["owner"]:
            who = uploader_term(self.engine, self.plan["owner"])
            if who and asc:
                lo, hi = self.plan["lo"], self.plan["hi"]
                walk_lanes = {"inside": self._lane(_query(who, f"id:>{lo - 1}", f"id:<{hi + 1}", no_video, asc)),
                              "after": self._lane(_query(who, f"id:>{hi}", no_video, asc)),
                              "before": self._lane(_query(who, f"id:<{lo}", no_video))}
            else:
                self.notes.append(f"{self.site}: загрузившего не назвать в поиске — его посты вокруг не спрашиваются")
        work_lanes = [(key, self._lane(_query(f"source:*{text}*", no_video)))
                      for key, text in set_seek.work_queries(data["ids"], data["queries"])]
        family = None
        if self.parent:
            family = self._family(self.parent)
            self._families.append(family)
        elif self.kind == "parent" and self.plan.get("root"):
            self.notes.append(f"{self.plan['root'][0]} нет среди сайтов «Галереи (booru)» или он выключен — семья не "
                              "спрашивается")
        lanes = list(walk_lanes.values()) + [lane for _key, lane in work_lanes]
        try:
            with ThreadPoolExecutor(max_workers=6, thread_name_prefix="set-parts") as pool:
                jobs = [pool.submit(lane.pages, INSIDE_PAGES if lane is walk_lanes.get("inside") else 1) for lane in lanes]
                if family is not None:
                    jobs.append(pool.submit(family.load))
                for job in jobs:
                    job.result()
            found_posts = self._gather(own, walk_lanes, work_lanes, family, lanes)
        except Exception:
            for lane in lanes:
                lane.close()
            raise
        self._keep(found_posts, lanes, family)

    def _gather(self, own: dict, walk_lanes: dict, work_lanes: list, family, lanes: list) -> list:
        """The posts that are parts, in the order found: (post, search, card)."""
        from concurrent.futures import ThreadPoolExecutor
        from core import set_seek
        data = self.plan["data"]
        where: dict = {}                       # post number -> (post, search, card) as a search brought it
        for lane in lanes:
            for post, card in lane.posts:
                where.setdefault(post.id, (post, lane.search, card))
        fam_posts: list = []
        if family is not None:
            for search, card in family._found.values():
                for entry in card["entries"]:
                    if entry.get("site") == self.site:
                        post = set_seek.site_post(entry, self.engine)
                        if post is not None:
                            where.setdefault(post.id, (post, search, card))
                            fam_posts.append(post)
        # the сборка's own posts as the site gave them (with what the library knows of them more), in its order
        order = {image_id: n for n, image_id in enumerate(data["ids"])}
        pages = [set_seek.joined(where[p.id][0], p) if p.id in where else p
                 for p in sorted(own.values(), key=lambda p: (order.get(p.card.id, 1 << 30), p.id))]
        found: list = list(fam_posts)
        if walk_lanes:
            found += self._walk(pages, walk_lanes, where)
        # the same work by its source; the artist's posts of the same canvas (a set of pictures)
        own_ids = set(own)
        hits: list = []
        for key, lane in work_lanes:
            hits += set_seek.work_hits(key, [p for p, _c in lane.posts], own_ids)
        anchor = pages[0] if pages else None
        # the site answered none of the first questions (v5.44.152): the next ones are not asked — the page waits
        deaf = bool(lanes or family is not None) and all(lane.unheard for lane in lanes) and \
            (family is None or family.unheard)
        canvas = set_seek.canvases(pages, anchor) if anchor is not None and not deaf else []
        asc, no_video = _ASC.get(self.engine, ""), _NO_VIDEO.get(self.engine, "")
        canvas_lanes = [(page, w, h, self._lane(_query(art, f"width:{w}", f"height:{h}", no_video)))
                        for page, art, w, h in canvas]
        lanes += [lane for *_x, lane in canvas_lanes]
        artist_lanes: dict = {}
        comic = set_seek.comic_tags(pages + found) if walk_lanes and self.engine != "danbooru" and not deaf else None
        if comic:
            known = [p.id for p in pages + found]
            lo, hi = min(known), max(known)
            base = _query(comic[0], comic[1], no_video)
            artist_lanes = {"inside": self._lane(_query(base, f"id:>{lo}", f"id:<{hi}", asc)),
                            "after": self._lane(_query(base, f"id:>{hi}", asc)),
                            "before": self._lane(_query(base, f"id:<{lo}"))}
            lanes += list(artist_lanes.values())
        if canvas_lanes or artist_lanes:
            with ThreadPoolExecutor(max_workers=5, thread_name_prefix="set-parts") as pool:
                jobs = [pool.submit(lane.pages, 1) for *_x, lane in canvas_lanes]
                jobs += [pool.submit(lane.pages, ARTIST_INSIDE_PAGES if name == "inside" else 1)
                         for name, lane in artist_lanes.items()]
                for job in jobs:
                    job.result()
            for lane in [lane for *_x, lane in canvas_lanes] + list(artist_lanes.values()):
                for post, card in lane.posts:
                    where.setdefault(post.id, (post, lane.search, card))
        taken = own_ids | {p.id for p in found}
        for page, w, h, lane in canvas_lanes:
            hits += set_seek.canvas_hits(page, anchor, w, h, [p for p, _c in lane.posts], taken)
        if artist_lanes:
            walk = set_seek.Walk(pages + found, anchor)
            found += walk.comic_by_artist(*[[p for p, _c in artist_lanes[name].posts] for name in ("inside", "after", "before")])
        seen = own_ids | {p.id for p in found}
        hits = [p for n, p in enumerate(hits) if p.id not in seen and all(q.id != p.id for q in hits[:n])]
        found += hits
        # the families of what the source found (a minicomic uploaded by others as families)
        roots: list = []
        for post in hits:
            root = post.parent or post.id
            if root != self.parent and root not in roots:
                roots.append(root)
        families = [self._family(root) for root in roots[:WORK_FAMILIES]]
        if families:
            self._families += families
            with ThreadPoolExecutor(max_workers=len(families), thread_name_prefix="set-parts") as pool:
                for job in [pool.submit(fam.load) for fam in families]:
                    job.result()
            for fam in families:
                for search, card in fam._found.values():
                    for entry in card["entries"]:
                        post = set_seek.site_post(entry, self.engine) if entry.get("site") == self.site else None
                        if post is not None and post.id not in seen:
                            where.setdefault(post.id, (post, search, card))
                            found.append(where[post.id][0])
                            seen.add(post.id)
        drop = set_seek.one_file_copies(pages, found) | own_ids
        out, kept = [], set()
        for post in found:
            if post.id in drop or post.id in kept or post.id not in where:
                continue
            kept.add(post.id)
            out.append(where[post.id])
        errors = [lane.error for lane in lanes if lane.error] + [fam.error for fam in self._families if fam.error]
        if not out and errors:
            self.error = errors[0]
        self.unheard = any(lane.unheard for lane in lanes) or any(fam.unheard for fam in self._families)
        return out

    def _walk(self, pages: list, walk_lanes: dict, where: dict) -> list:
        """The uploader's posts around the сборка's (the reference client's ``autoSet``): between its posts, after, before; the
        pages of a comic passed on the way."""
        from core import set_seek
        owner = self.plan["owner"]
        mine = [p for p in pages if p.owner == owner] or pages
        if not mine:
            return []
        anchor = min(mine, key=lambda p: p.id)
        walk = set_seek.Walk(pages, anchor)
        walk.inside([p for p, _c in walk_lanes["inside"].posts])
        ahead = lambda: max((p for p in walk.pages if p.owner == owner), key=lambda p: p.id, default=anchor)
        behind = lambda: min((p for p in walk.pages if p.owner == owner), key=lambda p: p.id, default=anchor)
        walk.outward(ahead(), walk_lanes["after"].walk(walk.more))
        grew = walk.outward(behind(), walk_lanes["before"].walk(walk.more))
        if grew and any(p.card.comic for p in walk.pages):
            # the pages found back bring their characters: forward again over what came (nothing asked)
            walk.outward(ahead(), iter([p for p, _c in walk_lanes["after"].posts]))
        known = [p for lane in walk_lanes.values() for p, _c in lane.posts]
        walk.comic_between(known)
        for lane in walk_lanes.values():
            for post, card in lane.posts:
                where.setdefault(post.id, (post, lane.search, card))
        return list(walk.found)

    def _keep(self, found_posts: list, lanes: list, family) -> None:
        """Only the parts are kept: of every search its cards that are parts (the rest of the answers forgotten), the
        searches with none closed."""
        cards: list = []
        for _post, search, card in found_posts:
            if card["key"] not in self._found:
                self._found[card["key"]] = (search, card)
                cards.append(card)
        keys = set(self._found)
        searches = {id(search) for search, _card in self._found.values()}
        for lane in lanes:
            if id(lane.search) in searches:
                lane.search.keep_only(keys)
                self._lanes.append(lane)
            else:
                lane.close()
        self.cards = sorted(cards, key=self.rank)


def parts(settings: dict, set_id: int) -> SetParts:
    """The parts of the сборка ``set_id`` — asked now, or kept from a while ago (:data:`FAMILY_TTL`; asked again when
    the сборка's kind, site, uploader or the range of its posts changed)."""
    from core import card_sets
    data = card_sets.set_input(settings, set_id)
    if data is None:
        raise FamilyError("Такой сборки больше нет")
    asked_plan = plan(settings, data)
    key = parts_key(set_id)
    now = time.time()
    with _LOCK:
        _forget_old(now)
        found = _FAMILIES.get(key)
        if found is not None and getattr(found, "signature", None) != asked_plan["signature"]:
            _FAMILIES.pop(key).close()
            found = None
        if found is None:
            quiet = _quiet(asked_plan["site"], now) if asked_plan["site"] else ""
            if quiet:
                raise FamilyError(quiet)
            found = _FAMILIES[key] = SetParts(settings, asked_plan)
            while len(_FAMILIES) > KEEP:
                _old_key, old = _FAMILIES.popitem(last=False)
                old.close()
        else:
            _FAMILIES.move_to_end(key)
    found.load()
    _heard(found)
    return found


def group(settings: dict, key: str):
    """The parts an address's ``f=…`` names: a сборка's (``set-N``; kept as it was found — the page asks about one of
    its parts), or a family's of v5.30 (``site:parent``)."""
    set_id = parse_parts_key(key)
    if set_id is not None:
        with _LOCK:
            found = _FAMILIES.get(str(key))
            if found is not None and time.time() - found.asked <= FAMILY_TTL:
                _FAMILIES.move_to_end(str(key))
            else:
                found = None
        if found is not None:
            found.load()
            return found
        return parts(settings, set_id)
    parsed = parse_key(key)
    if parsed is None:
        raise FamilyError("Неверный адрес части сборки")
    return family(settings, *parsed)


def places(members: list, post_ids: dict, missing: list, fam) -> list:
    """Where each missing part goes among the сборка's cards: [(card, the card id it stands before — 0: at the end)].
    The parts' order (a family's parent first, then by number) against the cards' post numbers on the parts' site
    (``post_ids``); the сборка's own order is kept (the person may have changed it)."""
    out = []
    for card in missing:
        rank = fam.rank(card)
        before = 0
        for member in members:
            number = int(post_ids.get(int(member["id"])) or 0)
            if number and (number != fam.parent, number) > rank:
                before = int(member["id"])
                break
        out.append((card, before))
    return out


def import_item(card: dict) -> dict:
    """The post as the browser extension hands it to the program (``BrowserImportManager.submit``)."""
    entry = next((e for e in card["entries"] if e.get("original")), card["entries"][0])
    groups = entry.get("groups") or {}
    if not any(groups.values()):
        groups = {"general": list(entry.get("tags") or [])}
    urls = [u for u in (entry.get("original"), entry.get("sample")) if u]
    return {"site": entry["site"], "post_id": str(entry["post_id"]), "post_url": entry.get("url") or "",
            "media_urls": list(dict.fromkeys(urls)), "md5": card.get("md5") or entry.get("md5") or "",
            "groups": groups, "width": int(entry.get("width") or 0), "height": int(entry.get("height") or 0),
            "format": str(entry.get("ext") or ""), "is_post_page": True, "identity_v": 2,
            "page_title": f"{entry.get('label') or entry['site']} #{entry['post_id']}"}
