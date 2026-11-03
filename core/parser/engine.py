"""Per-file parsing algorithm.

For every file, in this order, stopping at the first stage that finds tags:

1. the gallery already has these exact bytes           -> reuse its tags (no network)
2. exact MD5 on every enabled booru (in parallel)       -> all matching sites are merged
3. hints in the file name: booru MD5, pixiv id, post id, rule34 image key
                                                        -> accepted after a visual check
4. a visually identical, already tagged gallery image   -> reuse its tags (no network)
5. the same pixels on a Danbooru-like site (pixel_hash) -> the original's MD5 everywhere
6. reverse image search: Fluffle and e621 IQDB (seconds), IQDB (half a minute), Danbooru IQDB, Ascii2D, SauceNAO
   (its day's searches last), one after another until one finds (in a run every service has its own queue: see
   runner.py; the order: core/parser/reverse/__init__.py)
After a hit, the post's own MD5 is looked up on the remaining sites ("relay"),
so one reverse-search hit collects tags from every booru that has the same file.

In a run (core/parser/runner.py) stage 2 is asked by the sites' own queues — «lanes»: every site goes
through the files at its own pace, a file found on one site is saved at once and the later finds of
other sites are added to the card; the stages after 2 start only when every site has answered.  For
that the work of a file comes in three parts: ``open_file`` (stage 1 and what comes before the sites),
``ask`` (one site, one MD5) and ``after_sites`` (stages 3–5 — then a ReverseTicket); reverse search comes in
steps too: ``reverse_prepare`` (the picture sent), ``reverse_next`` (the next service), ``reverse_ask`` (one service,
in its own queue), ``reverse_take`` (what its answer gives: the posts behind its hits) and ``reverse_finish`` (the
file's answer).  ``process`` / ``quick`` / ``slow`` do the same in one thread (callers outside the runner).

Exact-MD5 questions are answered from a batch when the site's lane asked about many files at once
(core/parser/batch.py); otherwise one request per file and site.  Every definite answer of a site
(found / not there) is written to the site journal (site_scan_status).

"Not found" is written to Брак only when every source that had to be asked really
answered.  Temporary trouble (network, rate limit, quota, maintenance, a paused
source) on any of them defers the file to the next run instead.  Sources switched
off for the whole run because they need the user's action (API key, Cloudflare)
do not hold files back; the run summary lists them.  See core/parser/availability.py.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from core.parser import persist, reverse_journal, tally
from core.parser.context import ParserContext
from core.parser.http import Cancelled, HttpFailure
from core.parser.models import FileResult, Match, ReverseOutcome
from core.parser.sites import SiteUnavailable, site_for_url
from core.parser.util import host_of, md5_from_filename, norm_md5, post_id_from_url, short
from core.parser.verify import VisualChecker, distance, local_phashes
from core.tagger.site_frames import START, WORDS
from core.work_timing import (AFTER, AFTER_GALLERY, AFTER_NAME_MD5, AFTER_NAME_POST, AFTER_PIXEL_HASH,
                              AFTER_PIXEL_SITE, AFTER_PIXELS, AFTER_PIXIV, AFTER_R34_KEY, AFTER_RELAY, AFTER_SOURCE_MD5,
                              AFTER_SUB, FINDS, FINDS_POST, FINDS_SUB, JOURNAL, PIXIV_ORIGINALS, PIXIV_SEARCH, READ,
                              RELAY, RELAY_SUB, STAGE, STEPS)

_PIXIV_NAME_RE = re.compile(r"(?<!\d)(\d{6,10})_(?:p|ugoira)\d+", re.I)
_PIXIV_URL_RE = re.compile(r"pixiv\.net/(?:en/)?artworks/(\d+)|illust_id=(\d+)|pximg\.net/.*/(\d{6,10})_p\d+", re.I)
_POST_NAME_PATTERNS = (
    (re.compile(r"^yande\.re[ _-]+(\d+)", re.I), "yande.re"),
    (re.compile(r"^konachan\.com[ _-]+-?[ _]*(\d+)", re.I), "konachan.com"),
    (re.compile(r"^konachan\.net[ _-]+-?[ _]*(\d+)", re.I), "konachan.net"),
    (re.compile(r"^danbooru[ _-]+(\d+)", re.I), "danbooru.donmai.us"),
    (re.compile(r"^e621[ _-]+(\d+)", re.I), "e621.net"),
    (re.compile(r"^gelbooru[ _-]+(\d+)", re.I), "gelbooru.com"),
    (re.compile(r"^rule34[ _-]+(\d+)", re.I), "rule34.xxx"),
)
_SITE_ERRORS = (SiteUnavailable, HttpFailure)
_ART_HOSTS = ("twitter.com", "x.com", "fanbox.cc", "fantia.jp", "deviantart.com", "artstation.com",
              "nijie.info", "skeb.jp", "patreon.com", "tumblr.com", "baraag.net", "pawoo.net", "misskey.io",
              # (v5.44.14) the places Fluffle names
              "furaffinity.net", "bsky.app", "inkbunny.net", "weasyl.com", "furrynetwork.com", "newgrounds.com")
_TWEET_RE = re.compile(r"(?:twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com|fixupx\.com)/[^/?#]+/status(?:es)?/(\d+)",
                       re.I)
_BSKY_RE = re.compile(r"bsky\.app/profile/[^/?#]+/post/([a-z0-9]+)", re.I)
_FA_RE = re.compile(r"furaffinity\.net/(?:view|full)/(\d+)", re.I)
_SOURCE_RELAY_MAX = 3      # the art sites' pages of a picture looked up on the sites at most
_MAX_CANDIDATES = 6
NOMATCH_STATUSES = ("nomatch", "no_match", "downloaded_no_match")   # a file processed before that is in Брак
_NO_PIXEL_HASH = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"}
# (v5.44.12) a reduced copy (a site's sample or thumbnail: other pixels than any original) — no pixel_hash question
_REDUCED_NAME = re.compile(r"^(?:sample|thumbnail)[_-]", re.I)
KEY_GIVE_UP = 40            # rule34 keys in a row without a post (none found in the run): the run asks no more
# (v5.44, the order «SauceNAO первым») a file passes over a service with a daily quota (SauceNAO) whose queue would keep
# it longer than this, when a service after it would answer sooner: those are asked first, the quota's service after
# them (``reverse_next``).  In the order «IQDB первым» (the default since v5.44.3) it is last: nothing passes it over.
PASS_OVER_WAIT = 120.0
# (v5.44.29) a file no run finished (deferred, a run stopped, the program closed): a site that said «нет» about its MD5
# within these days is not asked about it again (the old program kept every site's answer, ``site_scan_status``, and
# never asked again; at the owner after a restart 4255 such files waited for gelbooru.com one by one — 1 h 16 min — and
# as long for rule34.xxx, ATF and the checks after the sites)
SITE_MISS_KEEP_DAYS = 7
# (v5.44.36) two pictures of a video for a service this near (pHash) are one picture: it is asked once
ALIKE = 6


def _picture_hash(path) -> str:
    """The pHash of a picture made for the services ("" — it cannot be read: then it is not compared)."""
    try:
        import imagehash
        from PIL import Image
        with Image.open(path) as picture:
            return str(imagehash.phash(picture.convert("RGB")))
    except Exception as exc:  # an odd copy: the service is still asked with it
        import logging
        logging.getLogger("local_booru.parser").info("pHash картинки для поиска не посчитан: %s: %s",
                                                     type(exc).__name__, exc)
        return ""


def _alike(a: str, b: str) -> bool:
    near = distance(a, b) if a and b else None
    return near is not None and near <= ALIKE


@dataclass
class FileJob:
    path: Path
    index: int
    original_path: str           # identity used in the database
    existing_media: str = ""     # NO_MATCH retry: the managed copy that already exists


@dataclass
class ReverseTicket:
    """A file whose quick stages (MD5, file name, gallery) found nothing: it goes to reverse search, so slow reverse
    searches never hold up the MD5 lookups of other files.  ``step`` — where it is in the services' order (the next
    one to look at), ``upload`` — the picture every service is sent (made once, ``reverse_prepare``), ``later`` — the
    services passed over for their long queue (v5.44), asked after the rest; ``held`` — a service gave the file no
    answer for a while (paused, its limit, the failure that paused it): a service with a daily quota after it is not
    asked about the file now (v5.44.3: SauceNAO's searches only for what IQDB looked at and did not find) — the file is
    deferred and asked again."""
    job: "FileJob"
    state: "_State"
    step: int = 0
    upload: object = None
    later: list = field(default_factory=list)
    held: str = ""
    remembered: dict | None = None       # (v5.44.18) services that answered about the file before: reverse_journal
    # (v5.44.36) a video's pictures by style (``ReverseService.video_styles``: its start, the frame e621 or Danbooru
    # makes its previews of) and their pHashes; the styles each service was asked with; a service to ask once more
    # with its next picture (it found nothing by the one before); the services that named sources of it
    uploads: dict = field(default_factory=dict)
    upload_hashes: dict = field(default_factory=dict)
    asked: dict = field(default_factory=dict)
    again: object = None
    sourced: set = field(default_factory=set)


@dataclass
class _State:
    md5: str
    path: Path
    tag: str
    phashes: tuple | None = None                    # ((pHash, where), …): a picture's one, a video's frames
    deferred_until: float = 0.0
    problems: list = field(default_factory=list)      # (source, reason): temporary trouble — the miss is not trustworthy
    source_only: list = field(default_factory=list)
    searched_md5: set = field(default_factory=set)
    journal: dict = field(default_factory=dict)     # site -> (outcome, md5, url): definite answers only
    adding: list = field(default_factory=list)      # «добор»: the sites added since the file was processed
    key_asked: bool = False                          # rule34 answered about the key in the name (v5.44.12)
    key_post: bool = False                           # …with a post (its picture may still be another)
    resumed: bool = False                            # (v5.44.29) no run finished it: the sites' «нет» is kept

    def problem(self, owner, text: str) -> None:
        self.problems.append((owner, text))


class ParserEngine:
    def __init__(self, ctx: ParserContext, sites: list, services: list, pool: ThreadPoolExecutor, batch=None):
        self.ctx = ctx
        self.sites = sites
        self.services = services
        self.pool = pool
        self.batch = batch                      # Md5Batch, or None: one request per file and site
        self._pixel_queues: dict = {}           # host -> PixelQueue (core/parser/pixels.py)
        self._md5_queues: dict = {}             # host -> Md5Queue (core/parser/site_queue.py)
        self._pixel_lock = threading.Lock()
        # (v5.44.12) rule34's key in a name: how it went in this run — 0 of 370 at the owner (31.10.2026), a file 26 s
        self._key_lock = threading.Lock()
        self._key_misses = 0                    # «пост не найден» in a row while none was found
        self._key_found = 0
        self._key_off = False
        self.visual = VisualChecker(ctx)
        self.trust_unverified_filename_md5 = ctx.flag("parser_trust_unverified_filename_md5", True)
        self.recheck_new_sites = ctx.flag("parser_recheck_new_sites", False)
        # «Добор» only for the files in Брак: a new site is their one chance, the found ones only get one more set of tags
        self.recheck_nomatch_only = ctx.flag("parser_recheck_nomatch_only", False)
        # Browser-companion imports pass the MD5 published by the post they came from.
        self.authoritative_md5s = [m for m in (norm_md5(x) for x in ctx.settings.get("_authoritative_site_md5s") or []) if m]
        # (v5.44.27) a run's counts of what would be said about every file in the same words (core/parser/tally.py):
        # the run sets it and says them every five minutes; None — every line is said (one file, ``process``)
        self.tally = None

    # ── entry points ────────────────────────────────────────────────────────
    def process(self, job: FileJob) -> FileResult:
        """The whole file in this thread (callers outside the runner)."""
        result = self.quick(job)
        return self.slow(result) if isinstance(result, ReverseTicket) else result

    def quick(self, job: FileJob):
        """Every stage except reverse search: a FileResult, or a ReverseTicket for the slow queue."""
        started: list = []                      # the file's _State once it has one
        handed_over = False
        try:
            result = self._quick(job, started)
            handed_over = isinstance(result, ReverseTicket)
            return result
        finally:
            if started and not handed_over:
                self._file_done(job, started[0])

    def abandon(self, ticket: ReverseTicket) -> None:
        """The run stopped before this file's reverse search: keep what the sites already answered."""
        self._file_done(ticket.job, ticket.state)

    def slow(self, ticket: ReverseTicket) -> FileResult:
        """Reverse search for a file the quick stages did not find, every service in this thread (callers outside the
        runner; a run gives each service a queue of its own)."""
        matches: list = []
        try:
            if self.reverse_prepare(ticket):
                service = self.reverse_next(ticket)
                while service is not None and not matches:
                    matches = self.reverse_take(service, ticket, self.reverse_ask(service, ticket))
                    service = None if matches else self.reverse_next(ticket)
        except BaseException:
            self._file_done(ticket.job, ticket.state)
            raise
        return self.reverse_finish(ticket, matches)

    # ── reverse search in steps (a run: every service's own queue, core/parser/runner.py) ──
    def reverse_services(self) -> list:
        """The services a run asks, in their order (core/parser/reverse/__init__.py: iqdb.org first, SauceNAO last —
        or the other way round, as chosen on «Сайты и ключи»)."""
        return [s for s in self.services if s.enabled()]

    def reverse_prepare(self, ticket: ReverseTicket) -> bool:
        """The picture every service is sent (a smaller JPEG), made once; False — the file cannot be searched (its
        answer is then a miss).  (v5.44.36) A video — a picture of each style its services are asked with
        (``ReverseService.video_styles``: its start, the frame e621 or Danbooru makes its previews of), the start —
        ``upload``.  (v5.44.18) Every service on answered about it before — nothing is made: ``reverse_next`` gives
        none, the file goes to its answer (the relay of its sources, Брак)."""
        services = [s for s in self.services if s.enabled() and not self._answered_before(ticket, s)]
        if not services:
            return True
        styles = [START]
        if self._is_video(ticket.state):
            for service in services:
                styles += [st for st in service.video_styles if st not in styles]
        for style in styles:
            made = self._upload_copy(ticket.state, style)
            if made is not None:
                ticket.uploads[style] = made
                ticket.upload_hashes[style] = _picture_hash(made)
        ticket.upload = ticket.uploads.get(START) or next(iter(ticket.uploads.values()), None)
        return ticket.upload is not None

    @staticmethod
    def _is_video(state: "_State") -> bool:
        from core.tagger.hashing import VIDEO_EXTS
        return state.path.suffix.lower() in VIDEO_EXTS

    def _style_now(self, service, ticket: ReverseTicket) -> str:
        """(v5.44.36) The picture of the file to send the service now: the first of its ``video_styles`` it was not
        asked with that is another picture than those it was (pHash farther than :data:`ALIKE`); a picture or a GIF —
        the file itself (:data:`START`).  "" — none left."""
        asked = ticket.asked.get(service.name, [])
        if not self._is_video(ticket.state):
            return "" if asked else START
        sent = [ticket.upload_hashes.get(st) for st in asked]
        for style in [st for st in service.video_styles if st in ticket.uploads] or [START]:
            if style in asked:
                continue
            if any(_alike(ticket.upload_hashes.get(style, ""), other or "") for other in sent):
                continue
            return style
        return ""

    def _ask_again(self, ticket: ReverseTicket, service) -> bool:
        """(v5.44.36) A video the service found nothing about by one picture is asked with its next one (another
        frame — the one another site makes its previews of): True — it goes to the service once more, nothing is
        remembered yet."""
        if not self._is_video(ticket.state):
            return False
        style = self._style_now(service, ticket)
        if not style:
            return False
        ticket.again = service
        if self.tally is not None:              # a run: counted, said every five minutes
            self.tally.count(tally.AGAIN, (service.name,))
        else:
            self._say_rev(ticket.state, f"{service.name}: этим кадром видео не нашёл — спрашиваю другим "
                                        f"({WORDS.get(style, style)})")
        return True

    # ── (v5.44.18) what the services answered before: core/parser/reverse_journal.py ──
    def _recall(self, ticket: ReverseTicket) -> dict:
        """The services' answers about the file kept from before (once a file); the source its Брак record keeps
        goes to the relay again — the service that named it may not be asked."""
        if ticket.remembered is not None:
            return ticket.remembered
        ticket.remembered = {}
        state = ticket.state
        try:
            if self.ctx.flag("parser_reverse_remember", True):
                ticket.remembered = reverse_journal.remembered(self.ctx.settings, ticket.job.original_path, self.services,
                                                               video=self._is_video(state))
            kept = reverse_journal.saved_source(self.ctx.settings, ticket.job.original_path)
        except Exception as exc:  # the memory saves time only: without it every service is asked, as before
            self._say(state, f"память обратного поиска недоступна ({type(exc).__name__}: {exc})")
            kept = None
        if kept and all(str(x.get("url") or "") != kept["url"] for x in state.source_only):
            state.source_only.append(kept)
        known = [s.name for s in self.services if s.enabled() and s.journal_key in ticket.remembered]
        if known and self.tally is not None:        # (v5.44.27) a run: counted, said every five minutes
            self.tally.known(known)
        elif known:
            self._say_rev(state, f"уже отвечали о нём раньше, не спрашиваю: {', '.join(known)}")
        return ticket.remembered

    def _say_rev_first(self, state: _State, text: str) -> None:
        """(v5.44.27) A line a run says about many files in the same words (a service's rest, its limit): said for the
        first file, counted for the rest — the run says how many more (core/parser/tally.py)."""
        if self.tally is None or self.tally.first(text, "[REVERSE:…]"):
            self._say_rev(state, text)

    def _answered_before(self, ticket: ReverseTicket, service) -> bool:
        return bool(service.journal_key) and service.journal_key in self._recall(ticket)

    def _remember(self, ticket: ReverseTicket, service, status: str, reason: str = "") -> None:
        try:
            reverse_journal.record(self.ctx.settings, ticket.job.original_path, service, status, reason,
                                   video=self._is_video(ticket.state))
        except Exception as exc:  # the memory saves time only: the answer itself is not changed by it
            self._say(ticket.state, f"ответ {service.name} не записан в память ({type(exc).__name__}: {exc})")

    def reverse_next(self, ticket: ReverseTicket, wait_of=None):
        """The next service to ask about the file, or None — none left.  One switched off for the run is passed
        over; one paused for a while too, and it makes the file's miss untrustworthy (the file is deferred).
        (v5.44) ``wait_of(service)`` — seconds a file would wait for the service now (its queue): a service with a
        daily quota (``quota_limited``: SauceNAO) is passed over while its queue would keep the file longer than
        :data:`PASS_OVER_WAIT` and a service after it would answer sooner — the file comes back to it after them, so
        the day's searches go to the files the others did not find (in the order «SauceNAO первым»; in «IQDB первым»,
        the default since v5.44.3, it is last anyway).  (v5.44.3) A service before it that gave the file no answer
        (paused, its limit) holds it: :meth:`_held`.  (v5.44.14) A service that may be throttled (``pass_over``:
        e621 IQDB without an account) is passed over the same way, and it comes back before a service with a daily
        quota: those searches go to what the others did not find.  (v5.44.36) A service that found nothing about a
        video by one of its pictures (``ticket.again``, :meth:`_ask_again`) comes first — with the next."""
        if ticket.again is not None:
            service, ticket.again = ticket.again, None
            if service.enabled() and service.paused():      # its next picture not asked: the miss is not trusted
                ticket.state.problem(service, service.pause_note())
                ticket.held = ticket.held or service.name
            elif service.enabled():
                return service
        while ticket.step < len(self.services):
            service = self.services[ticket.step]
            if getattr(service, "quota_limited", False):
                back = self._back_before_quota(ticket)
                if back is not None:
                    return back
            ticket.step += 1
            if not service.enabled() or self._answered_before(ticket, service):
                continue
            if service.paused():
                ticket.state.problem(service, service.pause_note())
                ticket.held = ticket.held or service.name
                continue
            if self._held(ticket, service):
                continue
            if wait_of is not None and (getattr(service, "quota_limited", False) or getattr(service, "pass_over", False)) \
                    and self._sooner_after(ticket, service, wait_of):
                ticket.later.append(service)
                continue
            return service
        while ticket.later:
            service = ticket.later.pop(0)
            if not service.enabled():
                continue
            if service.paused():
                ticket.state.problem(service, service.pause_note())
                continue
            if self._held(ticket, service):
                continue
            return service
        return None

    def _back_before_quota(self, ticket: ReverseTicket):
        """(v5.44.14) A service passed over for its long queue that has no daily quota (e621 IQDB, throttled), asked
        before a service that has one; None — none waits."""
        for i, service in enumerate(ticket.later):
            if getattr(service, "quota_limited", False):
                continue
            del ticket.later[i]
            if not service.enabled():
                return self._back_before_quota(ticket)
            if service.paused():                 # no answer of it: the file's miss is not trusted, the quota waits
                ticket.state.problem(service, service.pause_note())
                ticket.held = ticket.held or service.name
                return self._back_before_quota(ticket)
            return service
        return None

    def _held(self, ticket: ReverseTicket, service) -> bool:
        """(v5.44.3) A service with a daily quota is not asked about a file a service before it gave no answer for a
        while (``ticket.held``): its searches are for what the others looked at and did not find — the file is
        deferred (the others' trouble is noted) and both are asked again later."""
        if not ticket.held or not getattr(service, "quota_limited", False):
            return False
        self._say_rev_first(ticket.state, f"{service.name}: не спрашиваю, пока {ticket.held} не ответил — файл отложен")
        return True

    def _sooner_after(self, ticket: ReverseTicket, service, wait_of) -> bool:
        """The file would wait for ``service`` longer than :data:`PASS_OVER_WAIT`, and a service after it in the order
        (on, not paused) would answer sooner."""
        try:
            wait = float(wait_of(service))
        except Exception:  # an estimate that fails changes nothing: the usual order
            return False
        if wait <= PASS_OVER_WAIT:
            return False
        for other in self.services[ticket.step:]:
            if other is service or other in ticket.later or not other.enabled() or other.paused():
                continue
            try:
                if float(wait_of(other)) < wait:
                    return True
            except Exception:  # see above
                continue
        return False

    def reverse_ask(self, service, ticket: ReverseTicket) -> ReverseOutcome:
        """One service's answer about one file (in a run — its queue's thread): nothing of the file is changed here.
        A service switched off or paused meanwhile is not asked."""
        if service.disabled_reason:
            return ReverseOutcome("disabled", detail=service.disabled_reason)
        if service.paused():
            return ReverseOutcome("paused", detail=service.pause_note())
        self.ctx.http.check_cancel()
        self.ctx.activity(service.name, "поиск", str(ticket.state.path))
        style = self._style_now(service, ticket) or START          # (v5.44.36) a video: the picture of its style
        ticket.asked.setdefault(service.name, []).append(style)
        return service.search(ticket.uploads.get(style) or ticket.upload)

    def reverse_take(self, service, ticket: ReverseTicket, outcome: ReverseOutcome) -> list:
        """What a service's answer gives the file: its accepted finds (the posts behind the hits), or [] — the next
        service is asked.  A limit or a failure is the file's problem: a later miss is not trusted (deferred)."""
        state = ticket.state
        if outcome.status == "paused":
            state.problem(service, outcome.detail or service.pause_note())
            ticket.held = ticket.held or service.name
            return []
        if outcome.status == "deferred":
            state.deferred_until = max(state.deferred_until, float(outcome.retry_after or 0))
            state.problem(service, f"{service.name}: {outcome.detail or 'лимит'}")
            # (v5.44.27) a rest of the service (SauceNAO's after its server failed) defers every file that comes to it
            # in the same words: said for the first, counted for the rest
            self._say_rev_first(state, f"{service.name}: отложено — {outcome.detail}")
            ticket.held = ticket.held or service.name
            return []
        if outcome.status == "failed":
            service.note_failure(short(outcome.detail, 80) or "ошибка", transient=True)
            state.problem(service, f"{service.name}: {short(outcome.detail, 80)}")
            # the whole reason in the journal (v5.27 cut it at 120 letters: «…Multi-service image search ( 0…»)
            self._say_rev(state, f"{service.name}: сбой — {short(outcome.detail, 400)}")
            if service.paused():                # the failure that paused it: the service is down, not this file
                ticket.held = ticket.held or service.name
            return []
        if outcome.status != "complete":  # bad_input / disabled: no answer, nothing to wait for
            if outcome.detail and outcome.status != "disabled":
                self._say_rev(state, f"{service.name}: {outcome.status} — {short(outcome.detail, 120)}")
            if outcome.status == "bad_input":           # not an answer: asked again next time (reverse_journal)
                self._remember(ticket, service, "skipped", short(outcome.detail, 200))
            return []
        service.note_success()
        state.source_only += outcome.source_only
        if outcome.source_only:
            ticket.sourced.add(service.name)
        missed = "source_only" if service.name in ticket.sourced else "done_miss"
        if not self._is_video(state):           # (v5.44.36) weak hits (under the threshold) are for videos only
            outcome.candidates = [c for c in outcome.candidates if not getattr(c, "weak", False)]
        if not outcome.candidates:
            if not self._ask_again(ticket, service):        # (v5.44.36) a video: its next picture first
                self._remember(ticket, service, missed)
            return []
        troubles = len(state.problems)
        STAGE.prefix = FINDS_SUB                # (v5.35.1) the sites' answers about the hits are timed as its parts
        try:
            with STEPS.timed(FINDS):
                matches = self._resolve(outcome.candidates, service, state)
        finally:
            STAGE.prefix = ""
        if matches:
            self._remember(ticket, service, "done_match")
        elif len(state.problems) == troubles:   # its hits checked to the end — not a preview or a site that failed
            if not self._ask_again(ticket, service):
                self._remember(ticket, service, missed)
        return matches

    def reverse_finish(self, ticket: ReverseTicket, matches: list) -> FileResult:
        """The file's answer after reverse search: a find — the other sites asked for the post's MD5, the card saved;
        none — the art sites' pages it named looked up on the sites, else Брак (or deferred, when a service could not
        answer)."""
        try:
            if not matches:
                matches = self._source_relay(ticket.state)
            if matches:
                STAGE.prefix = RELAY_SUB            # the other sites' answers about the find are timed
                try:
                    with STEPS.timed(RELAY):
                        matches = list(matches) + self._relay(matches, ticket.state)
                finally:
                    STAGE.prefix = ""
                return self._finish_found(ticket.job, ticket.state, matches)
            return self._finish_miss(ticket.job, ticket.state)
        finally:
            self._file_done(ticket.job, ticket.state)

    # ── the same work in parts, for the sites' queues of a run (core/parser/runner.py) ──
    def open_file(self, job: FileJob):
        """What comes before the sites: (FileResult, None) when the file needs no site — processed before (and no
        site was added since), unreadable, blocked, the same bytes in the gallery — else (state, sites): the sites to
        ask about ``state.md5`` (a paused one is already noted as a problem of this file).  «Добор» (a file processed
        before, ``state.adding``): only the sites added since — asked by their queues like any file, in batches."""
        started: list = []
        try:
            with STEPS.timed(READ):
                prepared = self._prepare(job, started, lanes=True)
        except BaseException:
            if started:
                self._file_done(job, started[0])
            raise
        if isinstance(prepared, FileResult):
            if started:
                self._file_done(job, started[0])
            return prepared, None
        state = prepared
        if not self.ctx.flag("enable_md5_lookup", True):
            return state, []
        state.searched_md5.add(state.md5)
        # (v5.44.29) a site that said «нет» before is left out before the pauses are looked at: its pause now does not
        # hold the file (``_usable`` notes a paused site as the file's problem — the file would be deferred)
        on = [site for site in (state.adding or self.sites) if not site.disabled_reason]
        return state, self._usable(self._not_asked_again(job, state, on), state)

    def _not_asked_again(self, job: FileJob, state: _State, sites: list) -> list:
        """(v5.44.29) The sites to ask about a file no run finished (``state.resumed``): without those that said «нет»
        about this very MD5 within :data:`SITE_MISS_KEEP_DAYS` (the site journal, ``site_scan_status`` — a failure
        leaves no line there, so a site that could not answer is asked).  A run counts them (said every five minutes),
        without a run the file's line says them."""
        if not sites or state.adding or not state.resumed:
            return sites
        from core.database.storage import site_misses
        try:
            said = site_misses(self.ctx.settings, job.original_path, state.md5,
                               since=time.time() - SITE_MISS_KEEP_DAYS * 86400)
        except sqlite3.Error as exc:    # the journal only saves questions: without it every site is asked, as before
            self._say(state, f"журнал сайтов не прочитан ({type(exc).__name__}: {exc}) — спрашиваю все сайты")
            return sites
        kept = [site for site in sites if site.host not in said]
        if len(kept) == len(sites):
            return sites
        names = [site.label for site in sites if site.host in said]
        if self.tally is not None:
            self.tally.count(tally.SITES, names)
        else:
            self._say(state, f"сайты уже ответили «нет» об этом MD5 недавно — не спрашиваю снова: {', '.join(names)}")
        return kept

    def ask(self, site, md5: str, state: "_State"):
        """One site's answer about one MD5 of a file: its post, or None (not there, a problem — noted in
        ``state`` — or the site switched off).  Called by the site's own queue, one file after another."""
        if site.disabled_reason:
            return None
        if site.paused():
            state.problem(site, site.pause_note())
            return None
        return self._one_md5(site, md5, state)

    def save_found(self, job: FileJob, state: "_State", matches: list, late: bool = False) -> FileResult:
        """Finds of the sites into the library: the first find at once; ``late`` — finds of sites that answered
        after it: their tag sets join the card (``register_media_import`` adds to the card of the same bytes)."""
        return self._finish_found(job, state, list(matches), late=late)

    def after_sites(self, job: FileJob, state: "_State"):
        """Every site answered and none had the file: the stages after them — then a ReverseTicket (reverse
        search waits for its own workers) or the file's answer."""
        handed_over = False
        STAGE.prefix = AFTER_SUB                # its checks and the sites' answers are timed as its parts
        try:
            with STEPS.timed(AFTER):
                result = self._after_adding(job, state) if state.adding else self._after_md5(job, state, [])
            handed_over = isinstance(result, ReverseTicket)
            return result
        finally:
            STAGE.prefix = ""
            if not handed_over:
                self._file_done(job, state)

    def close_file(self, job: FileJob, state: "_State") -> None:
        """A found file whose sites have all answered: their answers go to the site journal."""
        with STEPS.timed(JOURNAL):
            self._file_done(job, state)

    def _file_done(self, job: FileJob, state: _State) -> None:
        if state.journal:
            from core.database.storage import mark_site_scanned_many
            rows = [{"original_path": job.original_path, "site_key": host, "engine": engine, "outcome": outcome,
                     "checked_md5": md5, "source_url": url} for host, (outcome, md5, url, engine) in state.journal.items()]
            try:
                mark_site_scanned_many(self.ctx.settings, rows)
            except Exception as exc:  # the journal speeds later runs up; the file itself is already saved
                self._say(state, f"журнал сайтов не записан: {type(exc).__name__}: {exc}")
        if self.batch is not None:
            self.batch.forget([state.md5, md5_from_filename(job.path.name)])

    def _quick(self, job: FileJob, started: list):
        prepared = self._prepare(job, started)
        if isinstance(prepared, FileResult):
            return prepared
        matches = self._md5_everywhere(prepared.md5, prepared, method="md5")
        return self._after_md5(job, prepared, matches)

    def _prepare(self, job: FileJob, started: list, *, lanes: bool = False):
        """Stage 1 and what comes before the sites: a FileResult when the file needs no site, else its _State
        (appended to ``started``).  ``lanes``: a file processed before comes back as a «добор» state (the sites'
        queues ask the new sites); without them «добор» is done here at once."""
        path = job.path
        log = lambda text: self.ctx.log(f"[MD5:{path.name}] #{job.index} {text}")
        status, resumed = self._status_of(job)
        if status:
            missing = self._sites_to_add(job) if self._adds_to(status) else []
            if not missing:
                return FileResult(path, "skip", f"уже в базе ({status})")
            return self._adding_state(job, missing, started) if lanes else self._add_sites(job, missing, started)
        md5 = self._file_md5(path)
        if not md5:
            log("не удалось прочитать файл")
            return FileResult(path, "error", "файл не читается")
        blocked = self._blocked(md5)
        if blocked:
            if self.tally is None or self.tally.first(f"пропуск — {blocked}", "[MD5:…]"):   # (v5.44.29) counted
                log(f"пропуск — {blocked}")
            return FileResult(path, "skip", blocked)
        if self.tally is not None:              # (v5.44.29) a run: counted, said every five minutes
            self.tally.count(tally.FILES)
        else:
            log(f"▶ md5={md5}")
        state = _State(md5=md5, path=path, tag=f"#{job.index}", resumed=resumed)
        started.append(state)

        bundle = persist.library_bundle_for_md5(self.ctx.settings, md5) if self.ctx.flag("skip_existing", True) else None
        if bundle:
            log("такой же файл уже есть в галерее — беру его теги")
            return self._finish_found(job, state, persist.matches_from_bundle(bundle, "library_md5"))
        return state

    def _after_md5(self, job: FileJob, state: "_State", matches: list):
        """Stages 3–6 after the sites' exact-MD5 answers ``matches``: the file's answer, or a ReverseTicket."""
        for site_md5 in self.authoritative_md5s:
            if not matches and site_md5 != state.md5:
                self._say(state, f"MD5 из поста-источника: {site_md5}")
                with STEPS.timed(AFTER_SOURCE_MD5):
                    matches = self._md5_everywhere(site_md5, state, method="site_md5")
        if not matches:
            matches = self._filename_hints(state)
        if not matches:
            with STEPS.timed(AFTER_GALLERY):
                matches = self._local_visual(state)
        if not matches:
            with STEPS.timed(AFTER_PIXELS):
                matches = self._pixel_hash(state)
        if matches:
            with STEPS.timed(AFTER_RELAY):
                matches += self._relay(matches, state)
            return self._finish_found(job, state, matches)
        if self._usable([s for s in self.services if s.enabled()], state):
            return ReverseTicket(job, state)
        return self._finish_miss(job, state)

    # ── new sites for files processed before («добор») ──────────────────────
    def _adds_to(self, status: str) -> bool:
        """«Добор новых сайтов» for a file processed before with this result: for every one, or — «только для
        Брака» — for those in Брак only."""
        if not self.recheck_new_sites:
            return False
        return not self.recheck_nomatch_only or str(status).lower() in NOMATCH_STATUSES

    def _sites_to_add(self, job: FileJob) -> list:
        """Enabled sites that never answered for this file (no line in the site journal)."""
        from core.database.storage import site_scan_hosts
        try:
            checked = site_scan_hosts(self.ctx.settings, job.original_path)
        except Exception as exc:  # without the journal nothing is known: leave the file as it is
            self.ctx.log(f"  журнал сайтов недоступен ({type(exc).__name__}: {exc})")
            return []
        return [s for s in self.sites if s.host not in checked and not s.disabled_reason]

    def _adding_state(self, job: FileJob, sites: list, started: list):
        """«Добор» of a file processed before: its _State with ``adding`` — the sites added since (MD5 only) — or
        a FileResult when the file cannot be asked (unreadable, deleted on purpose)."""
        path = job.path
        md5 = self._file_md5(path)
        if not md5:
            return FileResult(path, "skip", "файл не читается")
        blocked = self._blocked(md5)
        if blocked:
            return FileResult(path, "skip", blocked)
        state = _State(md5=md5, path=path, tag=f"#{job.index}", adding=list(sites))
        started.append(state)
        if self.tally is not None:              # (v5.44.29) a run: counted, said every five minutes
            self.tally.count(tally.ADDING, [s.label for s in sites])
        else:
            self._say(state, f"▶ добор новых сайтов ({', '.join(s.label for s in sites)}) md5={md5}")
        return state

    def _add_sites(self, job: FileJob, sites: list, started: list) -> FileResult:
        """A file processed before is looked up on the sites added since (MD5 only), here at once (callers
        outside the runner; a run asks them by the sites' queues).  A find joins the card (one more site's tags) or
        takes the file out of Брак; «not there» changes nothing."""
        state = self._adding_state(job, sites, started)
        if isinstance(state, FileResult):
            return state
        exclude = {s.host for s in self.sites} - {s.host for s in state.adding}
        matches = self._md5_everywhere(state.md5, state, method="md5", exclude=exclude)
        if matches:
            return self._finish_found(job, state, self._one_per_site(matches, state))
        return self._after_adding(job, state)

    def _after_adding(self, job: FileJob, state: _State) -> FileResult:
        """«Добор» after the new sites' exact-MD5 answers found nothing: the MD5 in the file name (on the same
        sites, with a look at the preview); then «not there» — the file stays as it was — or, when a site could not
        answer, deferred (the site journal has no line for it: the next run asks it again)."""
        names = ", ".join(s.label for s in state.adding)
        exclude = {s.host for s in self.sites} - {s.host for s in state.adding}
        name_md5 = md5_from_filename(state.path.name)
        if name_md5 and name_md5 != state.md5:
            matches = [m for m in self._md5_everywhere(name_md5, state, method="filename_md5", exclude=exclude)
                       if self._looks_like(state, m.post.preview_url)[0] is True]
            if matches:
                return self._finish_found(job, state, self._one_per_site(matches, state))
        live = list(dict.fromkeys(text for owner, text in state.problems if not getattr(owner, "disabled_reason", "")))
        if live:
            self._say(state, f"добор отложен — {'; '.join(live[:3])}")
            return FileResult(job.path, "deferred", "; ".join(live[:3]))
        return FileResult(job.path, "skip", f"на новых сайтах нет ({names})")

    # ── logging: prefixes understood by the parser page (channels, MD5 table) ──
    def _say(self, state: _State, text: str) -> None:
        self.ctx.log(f"[MD5:{state.path.name}] {state.tag} {text}")

    def _say_site(self, state: _State, label: str, text: str) -> None:
        self.ctx.log(f"[MD5:{label}:{state.path.name}] {text}")

    def _say_rev(self, state: _State, text: str) -> None:
        self.ctx.log(f"[REVERSE:{state.path.name}] {state.tag} {text}")

    # ── availability bookkeeping ────────────────────────────────────────────
    def _usable(self, sources: list, state: _State) -> list:
        """Sources that can be asked now. A paused one makes this file's miss untrustworthy."""
        out = []
        for src in sources:
            if src.disabled_reason:
                continue
            if src.paused():
                state.problem(src, src.pause_note())
                continue
            out.append(src)
        return out

    def _site_error(self, site, exc: Exception, state: _State, what: str = "", *, soft: bool = False) -> None:
        """Record a failed site request. Only temporary trouble defers the file."""
        transient = bool(getattr(exc, "transient", True))
        reason = short(getattr(exc, "detail", None) or exc, 120)
        if transient or not soft:
            site.note_failure(reason, transient=transient)
        if transient:
            state.problem(site, f"{site.label}: {reason}")
        prefix = f"{what}: " if what else ""
        self._say_site(state, site.label, f"{prefix}недоступен — {reason}")

    def _preview_problem(self, state: _State, post, detail: str, transient: bool) -> None:
        if transient:
            state.problem(self.visual, f"превью {post.label} {post.post_id}: {detail}")

    # ── stage helpers ───────────────────────────────────────────────────────
    def _status_of(self, job: FileJob) -> tuple[str, bool]:
        """(the result of the file's earlier processing that stands — "" when it is processed again, resumed): resumed —
        no run finished the file (deferred, a run stopped, the program closed), so the sites' «нет» about its MD5 is
        kept (v5.44.29, ``_not_asked_again``); not with «обрабатывать всё заново» nor for Брак asked again on purpose."""
        s = self.ctx.settings
        if not (s.get("skip_existing", True) or s.get("tag_only_untagged", True)):
            return "", False
        status = persist.processed_status(s, job.original_path)
        if not status:
            return "", True
        if status in NOMATCH_STATUSES and s.get("retry_nomatch"):
            return "", False
        return status, False

    def _file_md5(self, path: Path) -> str:
        from core.file_hash_cache import get_or_compute_md5
        try:
            return norm_md5(get_or_compute_md5(self.ctx.settings, path)[0])
        except OSError as exc:
            self.ctx.log(f"  {path.name}: {exc}")
            return ""

    def _blocked(self, md5: str) -> str:
        """Why this file must not come back into the library, or "" (see deleted_registry.reimport_block)."""
        if str(self.ctx.settings.get("deleted_reimport_policy", "skip")) == "return_inbox":
            return ""                                # the import pipeline brings the deleted card back
        from core.deleted_registry import reimport_block
        return {"trash": "этот файл в корзине",
                "deleted": "этот файл раньше удалён из библиотеки"}.get(reimport_block(md5, settings=self.ctx.settings), "")

    def _phash(self, state: _State) -> tuple:
        """What a site's preview is compared with: a picture's pHash, a video's or a GIF's frames (verify.py)."""
        if state.phashes is None:
            state.phashes = local_phashes(self.ctx, state.path)
        return state.phashes

    def _looks_like(self, state: _State, preview_url: str, *, max_distance: int | None = None):
        """``VisualChecker.check`` of the file and a preview — (v5.44.15) with the file itself, so a preview of a
        part of a wide or a tall picture (e621's) is compared with that part."""
        return self.visual.check(self._phash(state), preview_url, max_distance=max_distance, source=state.path)

    def _md5_everywhere(self, md5: str, state: _State, *, method: str, exclude: set | None = None) -> list:
        """Ask every usable site for this MD5 at once; each host keeps its own rate limit.  (v5.35) A site that takes
        packs gets it through its MD5 queue (``core/parser/site_queue.Md5Queue``: the files waiting for that site share
        one request, and no thread of the site pool waits for it); the others — one request each on the site pool."""
        md5 = norm_md5(md5)
        if not md5 or not self.ctx.flag("enable_md5_lookup", True):
            return []
        state.searched_md5.add(md5)
        sites = self._usable([s for s in self.sites if s.host not in (exclude or set())], state)
        prefix = STAGE.prefix                   # after the sites, the tags of a find: each site's answer is timed
        given = time.perf_counter()

        def single(site):
            started = time.perf_counter()
            if prefix:
                STEPS.add(prefix + "ожидание потока к сайтам", started - given)
            return False, site.search_md5(md5)
        asked = []
        for site in sites:
            known, post = self.batch.lookup(site, md5) if self.batch is not None else (False, None)
            if known:
                asked.append((site, None, (True, post)))
                continue
            self.ctx.activity(site.label, "MD5", str(state.path))
            if self.batch is not None and self.batch.on(site):
                future = self._md5_queue(site).ask(md5)
            else:
                future = self.pool.submit(single, site)
            if prefix:
                future.add_done_callback(lambda _done, label=str(site.label): STEPS.add(
                    prefix + "ответ " + label, time.perf_counter() - given))
            asked.append((site, future, None))
        matches = []
        for site, future, ready in asked:
            if future is not None:
                try:
                    ready = future.result()
                except _SITE_ERRORS as exc:
                    self._site_error(site, exc, state)
                    self.ctx.activity(site.label, "ошибка", str(state.path))
                    continue
                if not ready[0]:
                    site.note_success()
            post = self._md5_answer(site, md5, state, *ready)
            if post:
                matches.append(Match(post, method=method, verified="md5"))
        return matches

    def _one_md5(self, site, md5: str, state: _State):
        known, post = self.batch.lookup(site, md5) if self.batch is not None else (False, None)
        if not known:
            self.ctx.activity(site.label, "MD5", str(state.path))
            try:
                post = site.search_md5(md5)
            except _SITE_ERRORS as exc:
                self._site_error(site, exc, state)
                self.ctx.activity(site.label, "ошибка", str(state.path))
                return None
            site.note_success()
        return self._md5_answer(site, md5, state, known, post)

    def _md5_answer(self, site, md5: str, state: _State, known: bool, post):
        """A site's answer about an MD5 of a file into its journal and the run's: the post, or None."""
        how = " (пакетом)" if known else ""
        if post and post.tags:
            self._journal(state, site, "match", md5, post.url)
            if not known and self.batch is not None:
                self.batch.note_found(site, md5)
            self._say_site(state, site.label, f"✔ {tags_ru(len(post.tags))} {post.url}{how}")
            self.ctx.activity(site.label, f"найдено {len(post.tags)}", str(state.path))
            return post
        self._journal(state, site, "miss", md5, "")
        self.ctx.activity(site.label, "нет", str(state.path))
        return None

    @staticmethod
    def _journal(state: _State, site, outcome: str, md5: str, url: str) -> None:
        """One line per site and file: a find is never overwritten by a later «not there»; (v5.44.29) nor the site's
        «нет» about the file's own MD5 by its «нет» about another one (the MD5 in the name, a hit's post): a file no run
        finished is not asked about its MD5 again where the line says «нет» (``_not_asked_again``)."""
        before = state.journal.get(site.host)
        if before and before[0] == "match" and outcome != "match":
            return
        if before and outcome != "match" and before[1] == state.md5 and md5 != state.md5:
            return
        state.journal[site.host] = (outcome, md5, url, site.engine)

    def _accept(self, post, similarity: float, trust: float, state: _State, *, strict: bool = False) -> tuple[bool, str]:
        """Accept a reverse/hint candidate: high similarity, or a matching preview.  ``strict`` — a weak hit of a
        service that names other versions of a picture too (Fluffle under ``strict_below``): its preview must be
        nearly the file's (half the run's distance), another version of the picture does not pass."""
        if similarity >= trust:
            return True, f"{similarity:.0f}%"
        ok, detail, transient = self._looks_like(state, post.preview_url,
                                                 max_distance=self.visual.max_distance // 2 if strict else None)
        if ok is True:
            return True, f"{similarity:.0f}% + превью {detail}"
        if ok is False:
            self._say(state, f"✖ {post.label} {post.post_id}: превью не похоже ({detail})")
            return False, detail
        if similarity >= max(90.0, trust - 3):
            return True, f"{similarity:.0f}% (превью: {detail})"
        self._preview_problem(state, post, detail, transient)
        self._say(state, f"✖ {post.label} {post.post_id}: {similarity:.0f}% мало, а превью проверить нельзя ({detail})")
        return False, detail

    def _filename_hints(self, state: _State) -> list:
        name = state.path.name
        name_md5 = md5_from_filename(name)
        if name_md5 and name_md5 != state.md5:
            self._say(state, f"в имени файла другой MD5 ({name_md5}) — файл мог быть пережат, проверяю")
            accepted = []
            with STEPS.timed(AFTER_NAME_MD5):
                found = self._md5_everywhere(name_md5, state, method="filename_md5")
                # one file on every site: one preview tells for all (v5.35: was a preview of each site)
                ok, detail, transient = self._picture_of(state, found) if found else (None, "", False)
                for m in found:
                    if ok is True or (ok is None and self.trust_unverified_filename_md5):
                        m.verified = f"имя файла + превью {detail}"
                        accepted.append(m)
                        continue
                    if ok is None:
                        self._preview_problem(state, m.post, detail, transient)
                if found and not accepted:
                    self._say(state, f"✖ {', '.join(m.post.label for m in found)}: картинка не совпадает с MD5 из "
                                     f"имени ({detail})")
            if accepted:
                return self._one_per_site(accepted, state)
        m = _PIXIV_NAME_RE.search(name)
        if m:
            with STEPS.timed(AFTER_PIXIV):
                found = self._pixiv_relay(m.group(1), state, similarity=0.0, single_ok=True, hint=name)
            if found:
                return self._one_per_site(found, state)
        found = self._rule34_image_key(state)
        if found:
            return found
        return self._post_from_name(state, name)

    def _post_from_name(self, state: _State, name: str) -> list:
        """A post's number in the file's name (``danbooru_123``, ``yande.re 123``…): its post, when it is this file."""
        for pattern, host in _POST_NAME_PATTERNS:
            pm = pattern.search(name)
            if not pm:
                continue
            with STEPS.timed(AFTER_NAME_POST):
                found = self._post_by_number(state, pm.group(1), host)
            if found:
                return found
        return []

    def _post_by_number(self, state: _State, number: str, host: str) -> list:
        """The post ``number`` of the site ``host``, when it is this file (its MD5, or its preview)."""
        for site in self._usable([s for s in self.sites if s.host == host], state):
            try:
                post = site.fetch_post(number)
            except _SITE_ERRORS as exc:
                self._site_error(site, exc, state, "пост из имени файла")
                continue
            site.note_success()
            if not post or not post.tags:
                continue
            if post.md5 == state.md5:
                return [Match(post, method="filename_post_id", verified="md5")]
            ok, detail = self._accept(post, 0.0, 101.0, state)
            if ok:
                return [Match(post, method="filename_post_id", verified=detail)]
        return []

    def _rule34_image_key(self, state: _State) -> list:
        """``sample_<40 hex>.jpg``: rule34.xxx's own image key in the name finds the post even
        though a sample has another MD5 than the original.  (v5.44.12) :data:`KEY_GIVE_UP` keys in a row without a
        post, none found in the run — not asked any more in it (said once): at the owner 370 keys, no post, up to 26 s
        a file on the threads after the sites."""
        from core.tagger.filename_hints import extract_rule34_40hex_key
        key = extract_rule34_40hex_key(state.path.name)
        if not key or not self.ctx.flag("parser_rule34_image_key", True) or self._key_off:
            return []
        with STEPS.timed(AFTER_R34_KEY):
            found = self._rule34_key_post(state, key)
        self._key_result(bool(found) or state.key_post, asked=state.key_asked)
        return found

    def _key_result(self, found: bool, *, asked: bool) -> None:
        """Count a key's answer: :data:`KEY_GIVE_UP` posts not found in a row (none found in the run) — the run asks
        no more keys (the site, it seems, no longer finds a post by its picture's key)."""
        if not asked and not found:
            return                                  # the site was not asked (paused, an error): not its answer
        with self._key_lock:
            if found:
                self._key_found += 1
                self._key_misses = 0
                return
            self._key_misses += 1
            if self._key_found or self._key_off or self._key_misses < KEY_GIVE_UP:
                return
            self._key_off = True
        self.ctx.log(f"  ключ rule34 из имени: {KEY_GIVE_UP} файлов подряд — пост не найден, ни одного в этом запуске; "
                     "дальше в этом запуске не спрашиваю (rule34.xxx, похоже, больше не находит пост по ключу картинки)")

    def _rule34_key_post(self, state: _State, key: str) -> list:
        own = state.path.suffix.lower().lstrip(".")
        exts = list(dict.fromkeys(e for e in (own if own in ("png", "jpg", "jpeg", "gif", "webp") else "", "png", "jpeg") if e))
        for site in self._usable([s for s in self.sites if getattr(s, "is_rule34", False)], state):
            try:
                post_id = site.post_id_by_image_key(key, exts[:2])
                post = site.fetch_post(post_id) if post_id else None
            except _SITE_ERRORS as exc:
                self._site_error(site, exc, state, "ключ rule34 из имени", soft=True)
                continue
            site.note_success()
            state.key_asked = True
            if not post or not post.tags:
                self._say(state, f"ключ rule34 {key[:12]}…: пост не найден")
                continue
            state.key_post = True
            ok, detail, _transient = self._looks_like(state, post.preview_url)
            if ok is False:
                self._say(state, f"✖ ключ rule34 → пост {post_id}: превью не похоже ({detail})")
                continue
            self._say(state, f"✔ ключ rule34 из имени → пост {post_id}: {tags_ru(len(post.tags))}")
            return [Match(post, method="rule34_image_key", verified=f"ключ из имени {detail}".strip())]
        return []

    def _pixel_hash(self, state: _State) -> list:
        """Same pixels, other bytes (metadata stripped, PNG re-saved): Danbooru-like sites know the
        original by its pixel_hash; the original's MD5 is then looked up on every site.  (v5.34) Every site is asked
        at once, through its queue (``core/parser/pixels.py``: the files that wait for the site's turn go in one
        request when it understands a list)."""
        if not self.ctx.flag("parser_pixel_hash", True):
            return []
        sites = [s for s in self._danbooru_like(state) if not getattr(s, "pixel_hash_refused", "")]
        if not sites or state.path.suffix.lower() in _NO_PIXEL_HASH or _REDUCED_NAME.match(state.path.name):
            return []
        from core.tagger.hashing import danbooru_pixel_hash
        with STEPS.timed(AFTER_PIXEL_HASH):
            pixel_hash = norm_md5(danbooru_pixel_hash(state.path))
        if not pixel_hash or pixel_hash == state.md5:       # animated files: the hash is the file's MD5
            return []
        given = time.perf_counter()
        asked = []
        for site in sites:
            answer = self._pixel_queue(site).ask(pixel_hash)
            answer.add_done_callback(lambda _done, label=str(site.label): STEPS.add(
                AFTER_PIXEL_SITE + label, time.perf_counter() - given))   # each site's own wait (they go at once)
            asked.append((site, answer))
        from core.parser.pixels import SKIPPED
        for site, answer in asked:
            try:
                originals = answer.result()
            except _SITE_ERRORS as exc:
                if not getattr(exc, "transient", True):
                    # The site refuses media-asset searches (login, rights): not asked again this run.
                    site.pixel_hash_refused = short(getattr(exc, "detail", None) or exc, 80)
                    self.ctx.log(f"  {site.label}: поиск по pixel_hash недоступен ({site.pixel_hash_refused}) — "
                                 "до конца запуска не спрашиваю")
                    continue
                self._site_error(site, exc, state, "pixel_hash", soft=True)
                continue
            if originals is SKIPPED:
                self._say(state, f"{site.label}: пиксели не проверены — к нему длинная очередь (по одному файлу)")
                continue
            site.note_success()
            for md5 in originals:
                if md5 in state.searched_md5:
                    continue
                found = self._md5_everywhere(md5, state, method="pixel_hash")
                if found:
                    self._say(state, f"✔ те же пиксели, что у оригинала {md5} на {site.label}")
                    for m in found:
                        m.verified = "pixel_hash"
                    return self._one_per_site(found, state)
        return []

    def _md5_queue(self, site):
        """The site's queue of MD5 questions outside its own queue, for this run (made at the first question)."""
        from core.parser.site_queue import Md5Queue
        with self._pixel_lock:
            queue = self._md5_queues.get(site.host)
            if queue is None:
                queue = self._md5_queues[site.host] = Md5Queue(site, self.ctx.log, self.batch)
            return queue

    def _picture_of(self, state: _State, matches: list) -> tuple:
        """One look at the picture for posts of one MD5 (on every site the same file): (True / False / None, detail,
        transient) of the first preview that tells — the previews of the sites without a minute budget first (ATF
        has ten requests a minute for everything); the next only when one could not be compared."""
        from core.http_rate_limiter import requests_per_minute_for

        def cost(match) -> tuple:
            rpm = requests_per_minute_for(host_of(match.post.preview_url or ""), self.ctx.settings)
            return (0, 0) if rpm is None else (1, -int(rpm))
        verdict = (None, "нет превью", False)
        for match in sorted(matches, key=cost):
            verdict = self._looks_like(state, match.post.preview_url)
            if verdict[0] is not None:
                return verdict
        return verdict

    def _pixel_queue(self, site):
        """The site's queue of pixel_hash questions for this run (one, made at the first question)."""
        from core.parser.pixels import PixelQueue
        with self._pixel_lock:
            queue = self._pixel_queues.get(site.host)
            if queue is None:
                queue = self._pixel_queues[site.host] = PixelQueue(site, self.ctx.log)
            return queue

    def _local_visual(self, state: _State) -> list:
        if not self.ctx.flag("parser_local_visual_match", True):
            return []
        from core.tagger.local_phash_match import find_local_phash_match, load_local_match_bundle
        try:
            hit = find_local_phash_match(self.ctx.settings, state.path,
                                         max_distance=self.ctx.setting_int("parser_local_visual_distance", 4, 0, 12))
        except Exception as exc:  # optional shortcut: a broken cache must not stop the file
            self._say(state, f"локальный поиск похожих недоступен: {type(exc).__name__}: {exc}")
            return []
        if not hit:
            return []
        bundle = load_local_match_bundle(self.ctx.settings, int(hit["image_id"]))
        if not bundle.get("tags"):
            return []
        self._say(state, f"в галерее есть визуально тот же файл (d={hit['distance']}) — беру его теги")
        return persist.matches_from_bundle(bundle, "local_phash")

    def _upload_copy(self, state: _State, style: str = START):
        from core.tagger.hashing import VIDEO_EXTS, search_frame_image
        from core.tagger.reverse_upload import reverse_upload_image
        source = state.path
        if source.suffix.lower() in VIDEO_EXTS or source.suffix.lower() == ".gif":
            # (v5.44.36) the frame of a video the service's sites make their previews of (its start, e621's, Danbooru's)
            source = search_frame_image(source, settings=self.ctx.settings, log=None, style=style)
            if source == state.path and state.path.suffix.lower() in VIDEO_EXTS:
                if style == START:
                    self._say(state, "кадр из видео не извлечён — обратный поиск пропущен")
                return None
        try:
            return reverse_upload_image(source, self.ctx.settings, None)
        except Exception as exc:  # Pillow/OpenCV errors on odd files: skip reverse search for this file
            self._say(state, f"не удалось подготовить картинку для поиска: {type(exc).__name__}: {exc}")
            return None

    def _resolve(self, candidates: list, service, state: _State) -> list:
        """Best accepted hit wins; other sites come later from the MD5 relay."""
        seen = set()
        tried = 0
        for cand in sorted(candidates, key=lambda c: c.similarity, reverse=True):
            if tried >= _MAX_CANDIDATES:
                break
            # the same post often comes twice (…/posts/1 and …/post/show/1)
            key = (host_of(cand.url), post_id_from_url(cand.url) or cand.url)
            if key in seen:
                continue
            seen.add(key)
            tried += 1
            matches = self._candidate_matches(cand, service, state)
            if matches:
                kept = self._one_per_site(matches, state)
                for m in kept:
                    self._say_rev(state, f"{service.name} ✔ → {m.post.label}: {tags_ru(len(m.post.tags))} ({m.verified})")
                return kept
        return []

    def _candidate_matches(self, cand, service, state: _State) -> list:
        trust = service.trust_similarity
        method = service.name.lower().replace(" ", "_")
        if cand.url.startswith("md5:"):
            out = []
            for m in self._md5_everywhere(cand.url[4:], state, method=f"{method}_md5"):
                ok, detail = self._accept(m.post, cand.similarity, trust, state)
                if ok:
                    m.similarity, m.verified = cand.similarity, detail
                    out.append(m)
            return out
        pixiv = cand.pixiv_id or _pixiv_id_from_url(cand.url)
        if pixiv:
            if cand.preview_url and self._looks_like(state, cand.preview_url)[0] is False:
                return []
            return self._pixiv_relay(pixiv, state, similarity=cand.similarity, single_ok=cand.similarity >= 90,
                                     hint=cand.url)
        site = site_for_url(self.sites, cand.url)
        if site is None or not self._usable([site], state):
            return []
        started = time.perf_counter()
        try:
            post = site.post_from_url(cand.url)
        except _SITE_ERRORS as exc:
            self._site_error(site, exc, state, "пост из обратного поиска")
            return []
        finally:
            if STAGE.prefix == FINDS_SUB:
                STEPS.add(FINDS_POST + str(site.label), time.perf_counter() - started)
        site.note_success()
        if not post or not post.tags:
            return []
        ok, detail = self._accept(post, cand.similarity, trust, state,
                                  strict=getattr(cand, "weak", False)
                                  or cand.similarity < float(getattr(service, "strict_below", 0.0) or 0.0))
        return [Match(post, method=method, similarity=cand.similarity, verified=detail)] if ok else []

    def _one_per_site(self, matches: list, state: _State) -> list:
        """The library keeps one tag set per site: never send two posts of one site.

        The first match decides the picture; another site is added only with the
        very same file (same MD5) or a preview that matches the local image.
        """
        kept, hosts, md5s = [], set(), set()
        for m in matches:
            if m.post.site in hosts:
                continue
            if kept and not (m.post.md5 and m.post.md5 in md5s):
                if self._looks_like(state, m.post.preview_url)[0] is not True:
                    continue
            kept.append(m)
            hosts.add(m.post.site)
            if m.post.md5:
                md5s.add(m.post.md5)
        return kept

    def _danbooru_like(self, state: _State) -> list:
        return self._usable([s for s in self.sites if s.engine == "danbooru"], state)

    def _pixiv_relay(self, pixiv_id: str, state: _State, *, similarity: float, single_ok: bool, hint: str = "") -> list:
        """A pixiv artwork -> booru posts: ``pixiv_id:`` on Danbooru-like sites; else the MD5 of the
        artwork's originals on every site; else ``source:`` searches on the other sites."""
        found = self._pixiv_id_search(pixiv_id, state, similarity=similarity, single_ok=single_ok)
        if not found and self.ctx.flag("parser_pixiv_originals", True):
            found = self._pixiv_originals(pixiv_id, state, similarity=similarity, single_ok=single_ok, hint=hint)
        if not found:
            from core.parser import pixiv
            others = [s for s in self.sites if s.engine != "danbooru"]
            found = self._search_sources(pixiv.source_queries(pixiv_id), others, state,
                                         method="pixiv_source", what=f"pixiv {pixiv_id}")
        return found

    def _pixiv_originals(self, pixiv_id: str, state: _State, *, similarity: float, single_ok: bool, hint: str) -> list:
        from core.parser import pixiv
        try:
            with STEPS.timed(PIXIV_ORIGINALS):
                urls = pixiv.original_urls(self.ctx.http, pixiv_id, pixiv.page_hint(hint, state.path.name))
        except pixiv.PixivUnavailable as exc:
            self._say(state, f"pixiv {pixiv_id}: оригиналы не получены ({exc})")
            return []
        for url in urls:
            try:
                with STEPS.timed(PIXIV_ORIGINALS):
                    md5 = norm_md5(pixiv.md5_of_original(self.ctx.http, url))
            except pixiv.PixivUnavailable as exc:
                self._say(state, f"pixiv {pixiv_id}: оригинал не скачан ({exc})")
                return []
            if not md5 or md5 in state.searched_md5:
                continue
            accepted = []
            found = self._md5_everywhere(md5, state, method="pixiv_original")
            ok, detail, transient = self._picture_of(state, found) if found else (None, "", False)   # one file
            for m in found:
                if ok is True or (ok is None and single_ok and len(urls) == 1):
                    m.similarity, m.verified = similarity, f"оригинал pixiv {pixiv_id} {detail}".strip()
                    accepted.append(m)
                elif ok is None:
                    self._preview_problem(state, m.post, detail, transient)
            if accepted:
                self._say(state, f"✔ оригинал pixiv {pixiv_id} ({md5}) → "
                                 f"{', '.join(m.post.label for m in accepted)}")
                return self._one_per_site(accepted, state)
        return []

    def _search_sources(self, queries: list, sites: list, state: _State, *, method: str, what: str) -> list:
        """Tag-query searches (``source:…``) on these sites; a post counts only when its preview
        matches this file."""
        for site in self._usable(sites, state):
            for query in queries:
                try:
                    posts = [p for p in site.search_query(query, limit=5) if p.tags]
                except _SITE_ERRORS as exc:
                    self._site_error(site, exc, state, "поиск по источнику", soft=True)
                    break
                site.note_success()
                for post in posts:
                    ok, detail, transient = self._looks_like(state, post.preview_url)
                    if ok is True:
                        self._say(state, f"✔ {what} → {site.label} {post.post_id} ({detail})")
                        return [Match(post, method=method, verified=detail)]
                    if ok is None:
                        self._preview_problem(state, post, detail, transient)
        return []

    def _pixiv_id_search(self, pixiv_id: str, state: _State, *, similarity: float, single_ok: bool) -> list:
        """The work's posts on the Danbooru-like sites, one site after another (ATF last): the first site that has the
        picture is enough (v5.35.3) — the other sites get the post's MD5 with the find (``_relay``: their queues ask it
        in packs), and a search of ATF (ten requests a minute for everything) and its previews are not spent on it."""
        out = []
        prefix = STAGE.prefix
        for site in self._danbooru_like(state):
            if out:
                break
            started = time.perf_counter()
            try:
                posts = [p for p in site.search_query(f"pixiv_id:{pixiv_id}", limit=10) if p.tags]
            except _SITE_ERRORS as exc:
                self._site_error(site, exc, state, "pixiv-поиск", soft=True)
                continue
            finally:
                if prefix:                  # (after the sites, a reverse-search hit: its parts in «ВРЕМЯ»)
                    STEPS.add(prefix + PIXIV_SEARCH + str(site.label), time.perf_counter() - started)
            site.note_success()
            for post in posts:
                ok, detail, transient = self._looks_like(state, post.preview_url)
                if ok is True or (ok is None and single_ok and len(posts) == 1):
                    out.append(Match(post, method="pixiv_relay", similarity=similarity, verified=f"pixiv {pixiv_id} {detail}"))
                    self._say(state, f"✔ pixiv {pixiv_id} → {site.label} {post.post_id}: {tags_ru(len(post.tags))} ({detail})")
                    break
                if ok is None:
                    self._preview_problem(state, post, detail, transient)
        return out

    def _source_relay(self, state: _State) -> list:
        """Unsupported art-site hits (Twitter, Fanbox, FurAffinity, Bluesky…) -> ``source:`` search on every site."""
        urls = [str(x.get("url") or "") for x in sorted(state.source_only, key=lambda x: -float(x.get("similarity") or 0))]
        urls = list(dict.fromkeys(u for u in urls if any(h in host_of(u) for h in _ART_HOSTS)))[:_SOURCE_RELAY_MAX]
        for url in urls:
            found = self._search_sources(source_queries(url), self.sites, state, method="source_relay",
                                         what=f"источник {url}")
            if found:
                return found
        return []

    def _relay(self, matches: list, state: _State) -> list:
        """Look the found post's original MD5 up on every site that has not answered yet."""
        have = {m.post.site for m in matches}
        extra = []
        for md5 in {m.post.md5 for m in matches if m.post.md5} - state.searched_md5:
            found = self._md5_everywhere(md5, state, method="md5_relay", exclude=have)
            have |= {m.post.site for m in found}
            extra += found
        return extra

    # ── finishing ───────────────────────────────────────────────────────────
    def _finish_found(self, job: FileJob, state: _State, matches: list, *, late: bool = False) -> FileResult:
        result = persist.save_found(self.ctx.settings, job.path, matches, md5=state.md5,
                                    original_path=job.original_path)
        if not result.get("ok"):
            what = "теги ещё одного сайта не добавлены" if late else "ФИНАЛ ⚠ теги найдены, но не сохранены"
            self._say_rev(state, f"{what} — {result.get('reason')}")
            return FileResult(job.path, "error", str(result.get("reason")), matches)
        sites = ", ".join(sorted({m.post.label for m in matches}))
        # a later find of another site joins the card: not a second answer of the file («ФИНАЛ» is one per file)
        if late:
            self._say_rev(state, f"ДОБАВЛЕНО с {sites}: {tags_ru(result['tags'])}")
        else:
            self._say_rev(state, f"ФИНАЛ ✔ {tags_ru(result['tags'])} ({sites})")
        return FileResult(job.path, "tagged", sites, matches, int(result["tags"]), md5=state.md5)

    def _finish_miss(self, job: FileJob, state: _State) -> FileResult:
        # Problems of a source that has since been switched off for the run (bad key,
        # Cloudflare wall) do not hold the file back: retrying would not help.
        live = list(dict.fromkeys(text for owner, text in state.problems
                                  if not getattr(owner, "disabled_reason", "")))
        if live or state.deferred_until:
            when = time.strftime(" до %H:%M", time.localtime(state.deferred_until)) if state.deferred_until else ""
            reason = "; ".join(live[:3]) or "лимит сервиса"
            # (v5.44.35) the files a service's rest defers end alike: said for the first, counted for the rest (the
            # owner's journal of 2.11.2026: 385 of its 999 lines «ФИНАЛ ⏸ отложен до … — SauceNAO: пауза (сбой сервера 3)»)
            self._say_rev_first(state, f"ФИНАЛ ⏸ отложен{when} — {reason}")
            return FileResult(job.path, "deferred", reason, retry_at=float(state.deferred_until or 0.0))
        best = max(state.source_only, key=lambda x: float(x.get("similarity") or 0), default=None)
        saved = persist.save_nomatch(self.ctx.settings, job.path, original_path=job.original_path,
                                     existing_media=job.existing_media, source_only=best, log=self.ctx.log)
        if not saved.get("ok"):
            self._say_rev(state, f"ФИНАЛ ⚠ не найдено, в Брак не записан — {saved.get('reason')}")
            return FileResult(job.path, str(saved.get("status") or "error"), str(saved.get("reason")))
        extra = f" (есть источник без тегов: {best.get('url')})" if best and best.get("url") else ""
        self._say_rev(state, f"ФИНАЛ ✖ не найдено → Брак{extra}")
        return FileResult(job.path, "nomatch", "не найдено", md5=state.md5)


def tags_ru(n: int) -> str:
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        word = "тег"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "тега"
    else:
        word = "тегов"
    return f"{n} {word}"


def source_queries(url: str) -> list:
    """The ``source:`` searches that find a post by the page of its picture on an art site.  (v5.44.14) A tweet by its
    number — a post's source is a twitter.com or an x.com link, a reverse search may give the other one; a Bluesky post
    by its key — its profile is a name in a post's source and a «did:» in Fluffle's links; a FurAffinity one by its
    number — its page is …/view/<n>/ or …/full/<n>/; anything else by its address without the scheme.  A post another
    picture's (a longer number that begins the same) is left out by its preview (``_search_sources``)."""
    m = _TWEET_RE.search(url or "")
    if m:
        return [f"source:*status/{m.group(1)}*"]
    m = _BSKY_RE.search(url or "")
    if m:
        return [f"source:*post/{m.group(1)}*"]
    m = _FA_RE.search(url or "")
    if m:
        return [f"source:*furaffinity.net/*/{m.group(1)}*"]
    core = re.sub(r"^https?://(www\.)?", "", url or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return [f"source:*{core}*"] if core else []


def _pixiv_id_from_url(url: str) -> str:
    m = _PIXIV_URL_RE.search(url or "")
    return next((g for g in (m.groups() if m else ()) if g), "")


__all__ = ["ParserEngine", "FileJob", "Cancelled", "HttpFailure"]
