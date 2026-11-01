"""Questions to one site from many threads, asked by one thread of its own (v5.34–v5.35): while its request waits for
the site's turn (ATF: ten requests a minute for everything), the questions that come meanwhile go in its next request
together, when the site can take them so.

- :class:`Md5Queue` — the MD5 the stages after the sites and reverse search ask (the MD5 in a file's name, the tags of
  a find from the other sites): together in one ``md5:a,b,c`` when the run's 2/2 check proved the site understands it
  (``core/parser/batch.Md5Batch``), else one by one as before.  The sites' own queues (``runner._Lane``) ask their
  files in packs already; this is for the questions outside them.
- ``core/parser/pixels.PixelQueue`` — the pixel_hash of files no site had by MD5.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future

IDLE = 20.0                 # seconds a queue's thread waits for work before it ends


class SiteQueue:
    """One site's questions: ``ask`` — a Future of the answer; a subclass says how many go in one request (``_take``)
    and how they are asked (``_answer``: it sets every Future of the batch)."""

    def __init__(self, site, log):
        self.site = site
        self.log = log
        self._cond = threading.Condition()
        self._waiting: list = []            # [(question, Future)]
        self._since: list = []              # time.monotonic() each of them came (v5.44.12)
        self._thread: threading.Thread | None = None

    def ask(self, question) -> Future:
        future: Future = Future()
        with self._cond:
            self._waiting.append((question, future))
            self._since.append(time.monotonic())
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=f"parser-{type(self).__name__}-{self.site.host}",
                                                daemon=True)
                self._thread.start()
            self._cond.notify()
        return future

    def _take(self) -> int:
        return 1

    def _hold(self, waiting: int, oldest: float) -> float:
        """Seconds the next request waits for more questions (``waiting`` now, the oldest came ``oldest`` seconds
        ago); 0 — it goes now."""
        return 0.0

    def _run(self) -> None:
        while True:
            with self._cond:
                if not self._waiting:
                    self._cond.wait(IDLE)
                if not self._waiting:
                    self._thread = None
                    return
                hold = self._hold(len(self._waiting), time.monotonic() - self._since[0])
                if hold > 0:
                    self._cond.wait(hold)               # a new question wakes it: is the request full now?
                    continue
                take = max(1, int(self._take()))
                batch, self._waiting = self._waiting[:take], self._waiting[take:]
                self._since = self._since[take:]
            try:
                self._answer(batch)
            except BaseException as exc:            # never leave a file waiting for ever
                for _question, future in batch:
                    if not future.done():
                        future.set_exception(exc)

    def _answer(self, batch: list) -> None:
        raise NotImplementedError


class Md5Queue(SiteQueue):
    """The site's answer about an MD5: a Future of (answered by a pack, the post or None)."""

    def __init__(self, site, log, batch):
        super().__init__(site, log)
        self.batch = batch

    def _take(self) -> int:
        return self.batch.chunk(self.site) if self.batch.on(self.site) else 1

    def _answer(self, batch: list) -> None:
        md5s = list(dict.fromkeys(m for m, _f in batch))
        if len(md5s) > 1 and self.batch.on(self.site):
            self.batch.ask_site(self.site, md5s)     # a failed pack keeps nothing: each is asked alone below
        for md5 in md5s:
            futures = [f for m, f in batch if m == md5 and not f.done()]
            known, post = self.batch.lookup(self.site, md5)
            if not known:
                try:
                    post = self.site.search_md5(md5)
                except BaseException as exc:          # that file's trouble (the caller says it and decides)
                    for future in futures:
                        future.set_exception(exc)
                    continue
            for future in futures:
                future.set_result((known, post))
