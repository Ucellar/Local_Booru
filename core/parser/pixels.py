"""The same pixels as a site's original (v5.34): a file no site had by its MD5 may be an original saved again without
its metadata, or re-compressed losslessly — other bytes, the same pixels.  A Danbooru-like site knows the original by
its ``pixel_hash`` (``media_assets.json``).

Each site has one queue (:class:`PixelQueue` — a ``core/parser/site_queue.SiteQueue``): while its request waits for the
site's turn (ATF: ten requests a minute, shared with the MD5 queues and the tags of what was found), the files that
come meanwhile wait for the next request, and it asks them all at once (``search[pixel_hash_comma]=a,b,c``, up to
:data:`BATCH`).  A list is trusted only once the site proved it understands one: its answer to a list with the hash of
one of its own newest pictures in it (``newest_pixel_hashes``) had that picture and nothing that was not asked.  A site
that does not know the list answers with its newest pictures: then one file a request, as before.  (v5.35.1) The
newest picture may be an upload still in work, which a search does not show: a list without it is tried again with
the next one, up to :data:`PROOFS` times.  A site that answers even one file's search with pictures of other pixels
does not know the search at all: it refuses (nobody is asked again — its newest pictures are no one's originals).
Each request is timed for «ВРЕМЯ» (``core/work_timing.AFTER_PIXEL_ASK``), with how the site is asked after it.
(v5.35.2) The list goes as ``search[pixel_hash][]`` (``danbooru.md5s_by_pixel_hashes``); an error answer to a list
not proved yet is a failed proof too.  A site asked one file a request whose queue is long (ATF: 13 s a request, its
turn shared with everything else) is not waited for: a file whose wait would pass :data:`SKIP_AFTER` is not asked
there (:data:`SKIPPED`; «ВРЕМЯ» — ``AFTER_PIXEL_SKIP``) — a pixel find there is rare, and the files waiting for it held
the threads of the stages after the sites (5.35.1 at the owner: 107 s a file).
"""
from __future__ import annotations

import time
from concurrent.futures import Future

from core.parser.site_queue import SiteQueue
from core.work_timing import AFTER_PIXEL_ASK, AFTER_PIXEL_SKIP, STEPS

BATCH = 20                  # files in one request
PROOFS = 3                  # lists with one of the site's own pictures, before it is asked one file a request
SKIP_AFTER = 90.0           # seconds a file may wait for a site asked one file a request; longer — it is not asked


class _NotAsked(tuple):
    """The answer for a file not asked: no originals (it goes through a loop as an empty list)."""


SKIPPED = _NotAsked()       # the answer for a file not asked (its wait would have been longer)
UNKNOWN, MANY, ONE = "", "many", "one"


def _for_good(exc: BaseException) -> bool:
    """A refusal that will be the same for every file (rights, a search the site does not know)."""
    from core.parser.sites import SiteUnavailable
    return isinstance(exc, SiteUnavailable) and not getattr(exc, "transient", True)


class PixelQueue(SiteQueue):
    """The originals' MD5 of a pixel hash on the site: ``ask`` — a Future of [MD5] (a site error — its exception)."""

    def __init__(self, site, log):
        super().__init__(site, log)
        self.mode = UNKNOWN
        self.probes: list | None = None     # pixel hashes of the site's newest pictures ([] — it tells none)
        self.proofs = 0                     # lists that came back without the site's own picture
        self.refused: BaseException | None = None   # the site refuses the search: no file is asked again
        self.requests = 0
        self.files = 0
        self.pace = 0.0                     # seconds a request takes lately: the wait for the site's turn, its answer
        self.skipped = 0
        STEPS.note(self._step, "")          # (a run before this one may have said how it asked)

    def ask(self, question) -> Future:
        """As the queue's (a Future of [MD5]); asked one file a request, a file whose wait would pass
        :data:`SKIP_AFTER` gets :data:`SKIPPED` at once."""
        if self.mode == ONE and self.pace > 0:
            with self._cond:
                ahead = len(self._waiting) + (1 if self._thread is not None else 0)
            if (ahead + 1) * self.pace > SKIP_AFTER:
                self.skipped += 1
                STEPS.add(AFTER_PIXEL_SKIP + str(self.site.label), 0.0)
                future: Future = Future()
                future.set_result(SKIPPED)
                return future
        return super().ask(question)

    @property
    def _step(self) -> str:
        return AFTER_PIXEL_ASK + str(self.site.label)

    def _take(self) -> int:
        return 1 if self.mode == ONE else BATCH

    def _ask(self, fn, *args):
        """One request to the site — the wait for its turn and its answer, counted and timed for «ВРЕМЯ»."""
        started = time.perf_counter()
        try:
            return fn(*args)
        finally:
            spent = time.perf_counter() - started
            self.requests += 1
            self.pace = spent if not self.pace else 0.7 * self.pace + 0.3 * spent
            STEPS.add(self._step, spent)

    def _answer(self, batch: list) -> None:
        hashes = list(dict.fromkeys(h for h, _f in batch))
        self.files += len(hashes)
        found = self._many(hashes) if self.refused is None and len(hashes) > 1 and self.mode != ONE else None
        if found is not None:
            for h, future in batch:
                if not future.done():
                    future.set_result(list(found.get(h, [])))
            return
        for pixel_hash in hashes:               # one a request (the old way): an error is that file's
            futures = [f for h, f in batch if h == pixel_hash and not f.done()]
            try:
                if self.refused is not None:
                    raise self.refused
                md5s = self._ask(self.site.md5s_by_pixel_hash, pixel_hash)
            except BaseException as exc:
                if _for_good(exc):
                    self.refused = exc          # the files after it would get the same answer
                for future in futures:
                    future.set_exception(exc)
                continue
            for future in futures:
                future.set_result(list(md5s))

    def _many(self, hashes: list) -> dict | None:
        """{pixel hash: [MD5]} of the hashes asked at once, or None — these files one by one (the site does not
        understand a list: from now on; it has not proved it yet: until the next list)."""
        from core.parser.sites import SiteUnavailable
        asked, probe = list(hashes), ""
        if self.mode == UNKNOWN:
            if self.probes is None:
                try:
                    self.probes = list(self._ask(self.site.newest_pixel_hashes, PROOFS))
                except SiteUnavailable as exc:
                    if getattr(exc, "transient", True):
                        raise
                    self.probes = []            # its pictures are not listed (rights): one file a request
            if not self.probes:
                return self._one_by_one("сайт не говорит pixel_hash своих картинок")
            probe = self.probes[self.proofs % len(self.probes)]
            if probe not in asked:
                asked.append(probe)
        try:
            answer = self._ask(self.site.md5s_by_pixel_hashes, asked)
        except SiteUnavailable as exc:
            if not getattr(exc, "transient", True):
                return self._one_by_one(f"список отклонён: {exc}")
            if self.mode != UNKNOWN:
                raise
            # a list not proved yet that fails (an error page for a list it cannot read): a failed proof
            return self._proof_failed(f"на список ответил ошибкой: {exc}")
        if answer is None:
            return self._one_by_one("на список ответил чужими картинками")
        found, seen = answer
        if self.mode == UNKNOWN:
            if probe not in seen:
                return self._proof_failed("на список не ответил своей картинкой")
            self.mode = MANY
            STEPS.note(self._step, f"пачками, до {BATCH} файлов")
            self.log(f"  {self.site.label}: проверка пикселей — пачками, до {BATCH} файлов за запрос")
        return found

    def _proof_failed(self, why: str) -> None:
        """These files one by one; the next list — with another picture of the site, or none after :data:`PROOFS`."""
        self.proofs += 1
        if self.proofs >= min(PROOFS, len(self.probes or [])):
            return self._one_by_one(why)
        return None

    def _one_by_one(self, why: str) -> None:
        if self.mode != ONE:
            self.mode = ONE
            STEPS.note(self._step, f"по одному файлу: {why}")
            self.log(f"  {self.site.label}: проверка пикселей — по одному файлу ({why})")
        return None
