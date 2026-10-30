"""One grabber search: a tag query over the parser's sites that can search by tags, page after page.

Every site is a lane: «Ещё» asks each lane that still has posts for its next page (in parallel),
the posts become entries (``cards.entry``) and entries of one file (same MD5) one card.  Cards
are handed out in a mixed order (one post of each site in turn), never twice.  Not handed out:
posts with a tag from the user's blocklist (any site of the card), cards hidden with «Скрыть»,
and — when asked — what the library already has.

Searches are kept for the page by id (``open_search`` / ``get``): the previews, the post view and
the download need the cards of the search.  The oldest goes when there are more than
:data:`KEEP_SEARCHES`, or after :data:`SEARCH_TTL` of not being used.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor

from core.grabber.cards import OWNED_FILE, OWNED_POST, entry, is_hidden, mark_owned, new_card, tags_of, card_key
from core.parser.http import HttpClient

KEEP_SEARCHES = 6
SEARCH_TTL = 2 * 3600
PER_SITE = 40                    # posts asked from each site for one page
GIVE_UP_AFTER = 2                # pages in a row a site did not answer: it is left out of this search
ATTEMPTS = 2                     # tries of one request (the parser makes 3; a page waits for the slowest site)
_LOCK = threading.Lock()
_SEARCHES: "OrderedDict[str, GrabberSearch]" = OrderedDict()


def searchable_sites(settings: dict) -> list:
    """[(host, label)] — sites of the parser's table, switched on, whose engine pages through a
    tag search, in the table's order (the grabber asks them; the first is the preferred download)."""
    from core import site_table
    from core.parser.sites import clean_host, engine_class
    entries, order, _deleted = site_table.load(settings)
    out, seen = [], set()
    for key in order:
        cfg = entries.get(key)
        if not cfg or not cfg.get("enabled", True):
            continue
        host = clean_host(cfg.get("domain") or cfg.get("base_url") or "")
        cls = engine_class(cfg, host) if host else None
        if cls is None or not getattr(cls, "can_search_tags", False) or host in seen:
            continue
        seen.add(host)
        out.append((host, str(cfg.get("name") or host)))
    return out


class GrabberSearch:
    """``link`` — (host, may the host be a server of the site) of «ссылка вместо тегов» (``core.grabber.link``):
    the one site of that address, enabled or not, asked once; its card is shown even when it carries a tag of
    the blocklist (marked — it is not downloaded), was hidden, or the library has it."""

    def __init__(self, settings: dict, query: str, hosts: list | None = None, *, hide_owned: bool = False,
                 per_site: int | None = None, link: tuple | None = None):
        from core.grabber_exclusions import active_identity_set
        from core.parser.context import ParserContext
        from core.parser.sites import build_sites
        from core.subscriptions import normalize_blacklist_tags
        self.id = secrets.token_urlsafe(9)
        self.query = " ".join(str(query or "").split())
        self.settings = dict(settings)
        self.hide_owned = bool(hide_owned)
        self.per_site = max(1, int(per_site or PER_SITE))
        self.used = time.time()
        self.lock = threading.Lock()
        self.notes: deque = deque(maxlen=200)
        self.http = _GrabberHttp(self.settings, log=self._note, cancel_check=lambda: False, pause_wait=lambda: None)
        self.ctx = ParserContext(self.settings, self.http, self._note, lambda *_a: None)
        self.single = link is not None
        if self.single:
            from core.parser.sites import site_for_host
            site = site_for_host(self.ctx, link[0], parent_domains=bool(link[1]))
            if site is not None:
                site.preflight()
            self.sites = [site] if site is not None else []
            self.hide_owned = False
            self.per_site = min(self.per_site, 5)
        else:
            wanted = {str(h).lower() for h in hosts or [] if h}
            self.sites = [s for s in build_sites(self.ctx)
                          if getattr(s, "can_search_tags", False) and (not wanted or s.host in wanted)]
        self.lanes = {s.host: {"label": s.label, "page": 0, "posts": 0, "done": bool(s.disabled_reason),
                               "error": s.disabled_reason or "", "failures": 0} for s in self.sites}
        # The same threads for every «Ещё»: the client keeps a session per thread and site.
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(self.sites)), thread_name_prefix="grabber-site")
        self.cards: list = []                # handed out to the page, in order
        self.by_key: dict = {}               # every card met (shown or not)
        self._shown: set = set()
        blocklist = self.settings.get("grabber_subscriptions_blocklist") or self.settings.get("downloader_blocklist") or ""
        self.blocklist = set(normalize_blacklist_tags(blocklist))
        self.hidden = active_identity_set(self.settings)
        self.skipped = {"blocked": 0, "hidden": 0, "owned": 0}

    def _note(self, message: str) -> None:
        from core.redaction import sanitize_text
        self.notes.append(sanitize_text(message))

    # ── pages ───────────────────────────────────────────────────────────────
    @property
    def has_more(self) -> bool:
        return any(not lane["done"] for lane in self.lanes.values())

    def more(self) -> dict:
        """The next page of every site that still has posts: {"cards": new cards handed out,
        "updated": shown cards that got another site, "removed": shown cards that now carry a
        blocked tag}."""
        with self.lock:
            self.used = time.time()
            lanes = [s for s in self.sites if not self.lanes[s.host]["done"]]
            if not lanes:
                return {"cards": [], "updated": [], "removed": []}
            pages = list(self._pool.map(self._page, lanes))
            touched: list = []
            fresh: list = []
            for i in range(max((len(p) for p in pages), default=0)):
                for page in pages:              # one post of each site in turn
                    if i >= len(page):
                        continue
                    item = page[i]
                    key = card_key(item)
                    card = self.by_key.get(key)
                    if card is None:
                        card = self.by_key[key] = new_card(item)
                        fresh.append(card)
                    elif not any(e["site"] == item["site"] and e["post_id"] == item["post_id"] for e in card["entries"]):
                        card["entries"].append(item)
                        touched.append(card)
            if fresh or touched:              # a card a later page joined may be «есть» by its new post
                mark_owned(self.settings, fresh + [c for c in touched if c not in fresh])
            out = {"cards": [], "updated": [], "removed": []}
            for card in fresh:
                why = self._why_not_shown(card)
                if why:
                    self.skipped[why] += 1
                    continue
                self._shown.add(card["key"])
                self.cards.append(card)
                out["cards"].append(card)
            for card in touched:
                if card["key"] not in self._shown or card in out["cards"]:   # new now: sent whole anyway
                    continue
                why = self._why_not_shown(card)
                if why:                       # a blocked tag or «есть» came with the other site
                    self.skipped[why] += 1
                    self._shown.discard(card["key"])
                    self.cards = [c for c in self.cards if c["key"] != card["key"]]
                    out["removed"].append(card["key"])
                elif card not in out["updated"]:
                    out["updated"].append(card)
            return out

    def _page(self, site) -> list:
        from core.parser.http import HttpFailure
        from core.parser.sites import SiteUnavailable
        from core.redaction import sanitize_text
        lane = self.lanes[site.host]
        try:
            pairs = self._ask(site, lane["page"])
        except SiteUnavailable as exc:
            if site.disabled_reason or not exc.transient:      # a key, Cloudflare, a refusal: for good
                lane["error"] = sanitize_text(site.disabled_reason or str(exc))
                lane["done"] = True
            else:
                self._failed(lane, str(exc))
            return []
        except HttpFailure as exc:
            self._failed(lane, f"сеть: {exc.detail}")
            return []
        except Exception as exc:              # a site that answers nonsense must not take the search down
            lane["error"] = sanitize_text(f"{type(exc).__name__}: {exc}")
            lane["done"] = True
            return []
        lane["error"] = ""
        lane["failures"] = 0
        lane["page"] += 1
        lane["posts"] += len(pairs)
        if not pairs or self.single:              # a link: one answer is all there is
            lane["done"] = True
        out = []
        for post, raw in pairs:
            try:
                out.append(entry(site, post, raw))
            except (TypeError, ValueError, AttributeError) as exc:    # one odd post, not the page
                self._note(f"{site.host}: пост {getattr(post, 'post_id', '?')} пропущен: {exc}")
        return out

    def _ask(self, site, page: int) -> list:
        """A page of the site's tag search; for a link on a site without one (rule34.us) — its post's page, or the
        post the site finds by the file's MD5."""
        if self.single and not getattr(site, "can_search_tags", False):
            from core.grabber.link import raw_of
            post = None
            if not page and self.query.startswith("id:"):
                post = site.fetch_post(self.query[3:])
            elif not page and self.query.startswith("md5:"):     # a file's address: the site's own MD5 lookup
                post = site.search_md5(self.query[4:])
            return [(post, raw_of(post))] if post is not None else []
        return site.search_tags(self.query, page, self.per_site)

    @staticmethod
    def _failed(lane: dict, message: str) -> None:
        """A page the site did not give (network, limit, a server error): the next «Ещё» asks
        again — but after :data:`GIVE_UP_AFTER` in a row the site is left out of this search, or
        every «Ещё» would wait for it."""
        from core.redaction import sanitize_text
        lane["failures"] += 1
        lane["error"] = sanitize_text(message)
        if lane["failures"] >= GIVE_UP_AFTER:
            lane["done"] = True
            lane["error"] += " — не отвечает, в этом поиске пропущен"

    def blocked_tags(self, card: dict) -> list:
        """The card's tags that are in the grabber's blocklist (such a post is never downloaded)."""
        return sorted(self.blocklist & {t.lower() for t in tags_of(card)}) if self.blocklist else []

    def _blocked(self, card: dict) -> bool:
        return bool(self.blocked_tags(card))

    def _why_not_shown(self, card: dict) -> str:
        if self.single:                       # the person gave this post: shown, its marks say the rest
            return ""
        if self._blocked(card):
            return "blocked"
        if self.hidden and is_hidden(card, self.hidden):
            return "hidden"
        if self.hide_owned and card.get("owned") in (OWNED_FILE, OWNED_POST):
            return "owned"
        return ""

    # ── what the page asks about ────────────────────────────────────────────
    def card(self, key: str) -> dict | None:
        self.used = time.time()
        card = self.by_key.get(str(key or ""))
        return card if card is not None and card["key"] in self._shown else None

    def site(self, host: str):
        return next((s for s in self.sites if s.host == host), None)

    def hide(self, keys: list) -> int:
        """«Скрыть в граббере»: remembered for good (shared with the browser extension)."""
        from core.grabber.cards import identities
        from core.grabber_exclusions import add_exclusion, compact_identities
        done = 0
        with self.lock:                       # not while «Ещё» adds cards
            for key in keys:
                card = self.card(key)
                if card is None:
                    continue
                add_exclusion(self.settings, identities(card), reason="manual", query=self.query,
                              site=card["entries"][0]["site"])
                self.hidden.update(f"{typ}:{value}" for typ, value in compact_identities(identities(card)))
                self._shown.discard(card["key"])
                self.cards = [c for c in self.cards if c["key"] != card["key"]]
                done += 1
        return done

    def refresh_owned(self) -> None:
        """After a download: the marks of the shown cards again."""
        with self.lock:
            mark_owned(self.settings, self.cards)

    def keep_only(self, keys) -> None:
        """Forget every card but ``keys`` (a search kept for a few of its cards — the parts of a сборка,
        ``core/set_remote.py``: the rest of its answers is not held in memory)."""
        keep = set(keys)
        with self.lock:
            self.cards = [c for c in self.cards if c["key"] in keep]
            self.by_key = {k: c for k, c in self.by_key.items() if k in keep}
            self._shown &= keep

    def state(self) -> dict:
        return {"id": self.id, "query": self.query, "more": self.has_more, "shown": len(self.cards), "link": self.single,
                "skipped": dict(self.skipped),
                "sites": {host: {k: lane[k] for k in ("label", "posts", "done", "error")} for host, lane in self.lanes.items()}}

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.http.close_all()


class _GrabberHttp(HttpClient):
    """The parser's client with fewer tries: a page of results waits for its slowest site."""

    def request(self, method, url, **kwargs):
        kwargs["attempts"] = min(int(kwargs.get("attempts", 3) or 3), ATTEMPTS)
        return super().request(method, url, **kwargs)


# ── searches kept for the page ─────────────────────────────────────────────
def open_search(settings: dict, query: str, hosts: list | None = None, **kwargs) -> GrabberSearch:
    search = GrabberSearch(settings, query, hosts, **kwargs)
    with _LOCK:
        _SEARCHES[search.id] = search
        _drop_old()
    return search


def open_link(settings: dict, url: str) -> GrabberSearch:
    """A search of the one post at ``url`` (``core.grabber.link``); LinkError when the address cannot be one."""
    from core.grabber.link import LinkError, parse
    host, query, servers = parse(url)
    search = GrabberSearch(settings, query, link=(host, servers))
    if not search.sites:
        search.close()
        raise LinkError(f"С сайта {host} граббер брать не умеет: только с booru-сайтов (Danbooru, Gelbooru, rule34, "
                        "e621, Moebooru, rule34.us…). Свой booru-сайт можно добавить на странице «Сайты».")
    reason = search.sites[0].disabled_reason
    if reason:
        search.close()
        raise LinkError(f"{reason} — впишите это на странице «Сайты»")
    with _LOCK:
        _SEARCHES[search.id] = search
        _drop_old()
    return search


def get(search_id: str) -> GrabberSearch | None:
    with _LOCK:
        search = _SEARCHES.get(str(search_id or ""))
        if search is not None:
            _SEARCHES.move_to_end(search.id)
            search.used = time.time()
        return search


def _drop_old() -> None:
    now = time.time()
    for sid in [sid for sid, s in _SEARCHES.items() if now - s.used > SEARCH_TTL]:
        _SEARCHES.pop(sid).close()
    while len(_SEARCHES) > KEEP_SEARCHES:
        _sid, old = _SEARCHES.popitem(last=False)
        old.close()
