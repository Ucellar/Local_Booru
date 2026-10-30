"""Exact-MD5 answers for many files with one request per site.

Without it every file costs one request on every enabled site — for a big folder that is most of
the run.  Danbooru-like sites (Danbooru, ATF, aibooru…), e621 and Moebooru accept
``md5:a,b,c``; Gelbooru and rule34.xxx have OR syntaxes that their own docs call unreliable.

A batch answer can only say «not on this site» when the site really understood the query.  So
before the first batch, the site is asked for two MD5 it is known to have — from the site journal
(earlier exact matches, ``site_scan_status``); on a fresh library, from the site's own newest posts
(one request: their MD5 are surely there); failing that, from the first two single lookups that
found something in this run.  Only when both come back (2 of 2) are batches used; otherwise the files
are looked up one by one, exactly as before — and the check is made again on two files the run itself finds
there (a sample post deleted since, a moment of trouble on the site), ``CHECKS`` times in a run at most (before
v5.28 one failed check left a site one by one for the whole run: danbooru, through 31 568 files).

The run asks in batches from each site's own queue (core/parser/runner.py, ``_Lane``): the lane takes
up to ``chunk(site)`` waiting files, ``ask_site`` asks about all of them at once and keeps the answers,
and the per-file lookup (``lookup``, in the engine) takes its answer from there — or asks alone when the
batch could not tell.  An answer that cannot tell is asked again in halves first (``_ask``), so one post
that hides its MD5 costs a few requests instead of one per file of the batch.  An engine may know several
ways of asking (Gelbooru's OR syntax changed): the 2/2 check tries them in turn and keeps the one that passes.

Answers are mapped by the MD5 each post publishes, never by position.  An answer with a post
that hides its MD5 (Danbooru's restricted posts) or with a post nobody asked for proves nothing
about the missing ones: only its hits are kept, the rest is asked one by one.  A failed batch
(network, limit) keeps nothing — the single lookups that follow keep the usual rules (deferral,
pause), so a batch can never send a file to Брак.
"""
from __future__ import annotations

import threading

from core.parser.context import ParserContext
from core.parser.http import Cancelled, HttpFailure
from core.parser.sites import SiteUnavailable
from core.parser.util import norm_md5, short

ON, OFF, WAITING = "on", "off", "waiting"
RECENT_POSTS = 10            # newest posts asked for on a fresh library (some hide their MD5)
CHECKS = 3                   # 2/2 checks a site gets in a run: a failed one waits for two finds of the run to try again
SPLIT_LEAVE = 2              # an answer that cannot tell: this many files (or fewer) are asked one by one…
SPLIT_DEPTH = 6              # …more are asked again in halves, this deep at most (40 → 20 → 10 → 5 → 3)
_STYLE_SHOWN = {"braces": "{md5:… ~ md5:…}", "parens": "( md5:… ~ md5:… )", "tilde": "~md5:… ~md5:…"}


class Md5Batch:
    def __init__(self, ctx: ParserContext, sites: list, *, size: int = 40):
        self.ctx = ctx
        self.size = max(2, int(size))
        self.sites = [s for s in sites if getattr(s, "md5_batch_limit", 0) >= 2]
        self.mode: dict[str, str] = {}
        self._samples: dict[str, list] = {}           # host -> MD5 known to be on the site
        self._answers: dict[tuple, object] = {}        # (host, md5) -> Post, or None: surely not there
        self._lock = threading.Lock()
        self._probe_locks = {s.host: threading.Lock() for s in self.sites}
        self._failed: dict[str, int] = {}             # host -> 2/2 checks that did not pass in this run

    # ── capability ────────────────────────────────────────────────────────
    def start(self, pool) -> None:
        """Check every site that has a multi-MD5 query with two MD5 it is known to have (in parallel)."""
        futures = [pool.submit(self._start_site, site) for site in self.sites]
        for future in futures:
            future.result()

    def recheck(self, site) -> None:
        """A site switched on again in the middle of the run («Проверить снова»): its 2/2 check once more — it may
        have failed only because the site was walled off (Cloudflare) when the run started."""
        if site not in self.sites:
            return
        with self._probe_locks[site.host]:
            self._start_site(site)

    def _start_site(self, site) -> None:
        from core.database.storage import site_md5_samples
        if not site.ready():
            self.mode[site.host] = OFF
            return
        try:
            samples = site_md5_samples(self.ctx.settings, site.host, limit=4)
        except Exception as exc:  # the journal is an optimisation: without it the run waits for samples
            self.ctx.log(f"  {site.label}: журнал сайтов недоступен ({type(exc).__name__}: {exc})")
            samples = []
        if len(samples) >= 2:
            self._probe(site, samples)
            return
        # A fresh library: the site's newest posts are surely there.  If they do not prove the batch syntax (a post
        # nobody may see, a moment of trouble), the run still waits for two finds of its own, as before.
        recent = self._recent_md5s(site)
        if len(recent) >= 2 and self._probe(site, recent, final=False):
            return
        self.mode[site.host] = WAITING
        self.ctx.log(f"  {site.label}: пакетные MD5-запросы — жду двух найденных файлов для проверки")

    def _recent_md5s(self, site) -> list:
        """MD5 of the site's newest posts — surely on the site (a fresh library has no journal yet)."""
        if not getattr(site, "can_search_tags", False):
            return []
        try:
            rows = site.search_tags("", 0, RECENT_POSTS)
        except Cancelled:
            raise
        except (SiteUnavailable, HttpFailure) as exc:
            self.ctx.log(f"  {site.label}: новые посты не получены ({short(getattr(exc, 'detail', None) or exc, 80)})")
            return []
        except Exception as exc:  # an odd answer: the run simply waits for two finds
            self.ctx.log(f"  {site.label}: новые посты не прочитаны ({type(exc).__name__}: {exc})")
            return []
        out = []
        for post, _raw in rows or []:
            md5 = norm_md5(getattr(post, "md5", ""))
            if md5 and md5 not in out:
                out.append(md5)
        return out[:4]

    def _probe(self, site, samples: list, *, final: bool = True) -> bool:
        """2 of 2 known MD5 must come back; a second pair is tried when one of the first is gone, and every way of
        asking the engine has (``site.md5_batch_styles``: Gelbooru's new and old OR syntax) until one passes — that
        one is used for the run.  True — batches on; ``final`` False — a failed check decides nothing (the mode is
        left for the caller)."""
        verdict = "нет ответа"
        styles = tuple(site.md5_batch_styles()) or ("",)
        for style in styles:
            for pair in (samples[:2], samples[2:4]):
                if len(pair) < 2:
                    break
                try:
                    found, complete = site.search_md5_many(pair, style, probe=True)
                except Cancelled:
                    raise
                except (SiteUnavailable, HttpFailure) as exc:
                    verdict = f"ошибка: {short(getattr(exc, 'detail', None) or exc, 80)}"
                    break
                hits = sum(1 for m in pair if m in found)
                if hits == 2 and complete:
                    site.md5_batch_style = style
                    self.mode[site.host] = ON
                    where = "" if final else " на новых постах сайта"
                    how = f", запрос вида {_STYLE_SHOWN.get(style, style)}" if len(styles) > 1 and style else ""
                    self.ctx.log(f"  {site.label}: пакетные MD5-запросы включены (проверка 2/2{where}{how}), "
                                 f"до {self.chunk(site)} файлов за запрос")
                    return True
                verdict = f"{hits}/2" + ("" if complete else ", в ответе лишние посты")
        if not final:
            return False
        # A failed check is not the last word: a sample post may have been deleted or hidden since, the site may have
        # stumbled — up to ``CHECKS`` times in a run it is tried again on two files the run itself finds there.
        with self._lock:
            failed = self._failed[site.host] = self._failed.get(site.host, 0) + 1
            self._samples[site.host] = []
        if failed < CHECKS:
            self.mode[site.host] = WAITING
            self.ctx.log(f"  {site.label}: пакетные MD5-запросы пока не используются ({verdict}) — по одному файлу; "
                         f"проверю ещё раз на двух файлах, которые найдутся там в этом запуске")
            return False
        self.mode[site.host] = OFF
        self.ctx.log(f"  {site.label}: пакетные MD5-запросы не используются ({verdict}) — по одному файлу")
        return False

    def note_found(self, site, md5: str) -> None:
        """A single lookup found ``md5`` on ``site``: a sample for a site still waiting for two."""
        if self.mode.get(site.host) != WAITING:
            return
        with self._lock:
            samples = self._samples.setdefault(site.host, [])
            if md5 not in samples:
                samples.append(md5)
            ready = len(samples) >= 2
        if not ready:
            return
        with self._probe_locks[site.host]:
            if self.mode.get(site.host) == WAITING:
                with self._lock:
                    pair = list(self._samples.get(site.host, [])[:2])
                if len(pair) == 2:
                    self._probe(site, pair)

    def on(self, site) -> bool:
        """Batches are used for this site now."""
        return self.mode.get(site.host) == ON

    # ── answers ───────────────────────────────────────────────────────────
    def chunk(self, site) -> int:
        """How many MD5 one request of this site may carry."""
        return max(2, min(self.size, int(site.md5_batch_limit)))

    def ask_site(self, site, md5s: list) -> None:
        """Ask ``site`` about these MD5 in as few requests as it takes; the answers wait for ``lookup``.  A failed
        request keeps nothing (its files are asked one by one, with the usual deferral rules)."""
        with self._lock:
            todo = [m for m in dict.fromkeys(norm_md5(x) for x in md5s) if m and (site.host, m) not in self._answers]
        step = self.chunk(site)
        for start in range(0, len(todo), step):
            if not self._ask(site, todo[start:start + step], 0):
                return

    def _ask(self, site, md5s: list, depth: int) -> bool:
        """One request about ``md5s``.  An answer that cannot tell (a post hiding its MD5 — Danbooru's restricted
        posts — or one nobody asked for) proves nothing about the missing ones: they are asked again in halves, so
        the half without the hidden post is settled by one request instead of a request per file; the last two or
        so are left to single lookups (a single ``md5:`` search does find a hidden post).  False — the request
        failed: nothing more is asked in this batch."""
        self.ctx.activity(site.label, f"MD5 пакетом: {len(md5s)}", "")
        try:
            found, complete = site.search_md5_many(md5s)
        except Cancelled:
            raise
        except (SiteUnavailable, HttpFailure) as exc:
            self.ctx.log(f"  {site.label}: пакетный запрос не удался ({short(getattr(exc, 'detail', None) or exc, 100)})"
                         f" — {len(md5s)} файлов спрошу по одному")
            return False
        site.note_success()
        with self._lock:
            for md5 in md5s:
                if md5 in found:
                    self._answers[(site.host, md5)] = found[md5]
                elif complete:
                    self._answers[(site.host, md5)] = None
        if complete:
            return True
        rest = [m for m in md5s if m not in found]
        if len(rest) <= SPLIT_LEAVE or depth >= SPLIT_DEPTH:
            return True
        half = (len(rest) + 1) // 2
        return self._ask(site, rest[:half], depth + 1) and self._ask(site, rest[half:], depth + 1)

    def forget(self, md5s) -> None:
        """A file is done: answers nobody asked for (an MD5 from its name, when the real one was
        found) must not pile up over a long run."""
        keys = {norm_md5(m) for m in md5s if m}
        with self._lock:
            for key in [k for k in self._answers if k[1] in keys]:
                del self._answers[key]

    def lookup(self, site, md5: str) -> tuple[bool, object]:
        """(True, Post or None) when a batch already answered for this MD5 on this site."""
        key = (site.host, norm_md5(md5))
        with self._lock:
            if key in self._answers:
                return True, self._answers.pop(key)     # each answer is used once: memory stays flat
        return False, None
