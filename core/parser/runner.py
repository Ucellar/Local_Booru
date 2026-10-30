"""Run the parser over a folder: the sites' and services' own queues, workers, pause/stop, progress, summary.

Every MD5 site has its own queue — a «lane» (core/parser/lanes.py, one thread): it goes through the files at the
site's own pace, in batches when the site takes them (core/parser/batch.py), and never waits for
another site.  A file found on one site is saved at once (``engine.save_found``); a later find of a
slower site joins the card.  Only when every site has answered does a file go on: the stages after
the sites (file name, look-alikes, pixel hash) and reverse search — there every service has its own lane
too (v5.25): a file IQDB does not find goes on to SauceNAO's, and IQDB asks about the next files meanwhile;
what a service's answer gives (the posts behind its hits) and the saving are done by the reverse workers.
One loop (``_pass``) keeps the account of every file; the lanes and the workers only tell it what happened
(``events``).  ``estimate`` — how long the run still takes, by the lanes' own pace and by the pace of reading and
writing the files themselves (``Files``: on a big library the sites waited for files, not the files for the sites).

Threads are created once per run and every HTTP session is reused — memory stays flat no matter how
many files are processed: at most ``OPEN_FILES`` files wait for the sites' answers (the files waiting for
reverse search do not count: as before, MD5 lookups go on while reverse search catches up).
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from core.parser.batch import Md5Batch
from core.parser.context import ParserContext
from core.parser.engine import FileJob, ParserEngine, ReverseTicket
from core import work_timing
from core.parser.http import Cancelled, HttpClient, HttpFailure
from core.parser.lanes import (ASKING_AGAIN, OFF_HOW, Files, Open as _Open, ReverseLane, Share, SiteLane as _Lane,
                               WorkStage, estimate)
from core.parser.models import FileResult, Match
from core.parser.pool import READ, PriorityPool
from core.parser.reverse import build_reverse
from core.parser.sites import SiteUnavailable, build_sites
from core.parser.util import clean_host, short

__all__ = ["ParserRunner", "OPEN_FILES", "OFF_HOW", "ASKING_AGAIN", "MEDIA_EXTS"]

MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mkv", ".mov", ".avi"}
RETRY_MANIFEST = "_retry_nomatch_manifest.json"
OPEN_FILES = 20000           # files waiting for the sites' answers: how far a fast site may run ahead of a slow one
TIME_EVERY = 300.0           # seconds between the journal's lines «ВРЕМЯ …» (where the files' time went)
                             # (a site that takes no batches answers ~50 files a minute: the others go on meanwhile)


class ParserRunner:
    def __init__(self, settings: dict, *,
                 log: Callable[[str], None],
                 progress: Callable[[int, int], None] = lambda _d, _t: None,
                 current_file: Callable[[str], None] = lambda _p: None,
                 activity: Callable[[str, str, str], None] = lambda _s, _st, _p: None,
                 file_result: Callable[[str, FileResult], None] = lambda _p, _r: None):
        self.settings = dict(settings)
        self.log = log
        self.progress = progress
        self.current_file = current_file      # a file is taken (or goes on to reverse search)
        self.activity = activity
        self.file_result = file_result        # a file's answer: found, Брак, deferred (it may come again later), …
        self._stop = threading.Event()
        self._pause = threading.Event()
        self.counts: Counter = Counter()      # results so far: tagged / nomatch / deferred / skip / error
        self._lanes: list = []                # the sites' queues of the pass going now (``lanes()``)
        self._rlanes: list = []               # the reverse-search services' queues of the pass going now
        self._files: Files | None = None      # the files' own stages of the pass going now: reading…
        self._stages: tuple = ()              # …writing, the stages after the sites (``WorkStage``)
        self._flow: dict = {}                 # what ``estimate`` needs of the pass going now
        self._engine: ParserEngine | None = None      # the run going now (``site_again``)
        self._http: HttpClient | None = None
        self._again_lock = threading.Lock()

    # ── control (called from the UI thread) ─────────────────────────────────
    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause.set()
        else:
            self._pause.clear()

    def _hold_while_paused(self) -> None:
        """Worker threads wait here while paused (called before every request, never inside a lock)."""
        while self._pause.is_set() and not self.stopped():
            time.sleep(0.2)

    def stopped(self) -> bool:
        if self._stop.is_set():
            return True
        try:
            from core.shutdown import is_shutting_down
            return bool(is_shutting_down())
        except ImportError:
            return False

    # ── input ───────────────────────────────────────────────────────────────
    def collect(self) -> list:
        s = self.settings
        explicit = [Path(str(p)) for p in s.get("_parser_explicit_paths") or []]
        root = Path(str(s.get("root") or ""))
        files: list[Path] = []
        if explicit:
            for p in explicit:
                if p.is_dir():
                    files += [x for x in p.rglob("*") if x.suffix.lower() in MEDIA_EXTS]
                elif p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                    files.append(p)
        elif root.is_dir():
            files = [x for x in root.rglob("*") if x.suffix.lower() in MEDIA_EXTS and x.is_file()]
        else:
            self.log(f"ПАПКА НЕ НАЙДЕНА: {root}")
            return []
        files = sorted(set(files), key=lambda p: str(p).lower())
        self.log(f"НАЙДЕНО ФАЙЛОВ: {len(files)}")

        from core.tagger.results import has_copy_suffix, result_output_base
        out_base = result_output_base(s).resolve()
        try:
            resolved_root = root.resolve()
            scanning_output = resolved_root == out_base or out_base in resolved_root.parents
        except OSError:
            scanning_output = False
        manifest = _load_retry_manifest(root)
        if not scanning_output and not manifest:
            before = len(files)
            files = [p for p in files if out_base not in p.resolve().parents]
            if before - len(files):
                self.log(f"  пропущено файлов из рабочей библиотеки (output): {before - len(files)}")
        if s.get("skip_copy_suffix_files", True):
            before = len(files)
            files = [p for p in files if not has_copy_suffix(p)]
            if before - len(files):
                self.log(f"  пропущено копий вида «имя (1).jpg»: {before - len(files)}")
        limit = int(s.get("limit_files") or 0)
        if limit > 0:
            files = files[:limit]
        jobs = []
        for i, p in enumerate(files, 1):
            entry = manifest.get(str(p)) or manifest.get(p.name) or {}
            jobs.append(FileJob(path=p, index=i,
                                original_path=str(entry.get("original_path") or p),
                                existing_media=str(entry.get("media_path") or "")))
        return self._adding_order(jobs)

    def _adding_order(self, jobs: list) -> list:
        """With «Добор новых сайтов» the files processed before come after the new ones, and of them those in Брак
        first: a new site is their one chance, while a found file only gets one more set of tags — so a slow site
        (Gelbooru, one file at a time) reaches the files that need it most first.  In each group the folder order."""
        s = self.settings
        if not s.get("parser_recheck_new_sites") or not (s.get("skip_existing", True) or s.get("tag_only_untagged", True)):
            return jobs
        from core.database.storage import processed_kinds_many
        from core.parser.engine import NOMATCH_STATUSES
        try:
            kinds = processed_kinds_many(s, [job.original_path for job in jobs])
        except Exception as exc:  # the order is a convenience: without it the files go as they lie
            self.log(f"  порядок добора не выбран ({type(exc).__name__}: {exc})")
            return jobs
        retry = bool(s.get("retry_nomatch"))

        def group(job: FileJob) -> int:
            status = str(kinds.get(str(Path(job.original_path))) or "").lower()
            if not status or (retry and status in NOMATCH_STATUSES):
                return 0                                  # processed in full
            return 1 if status in NOMATCH_STATUSES else 2
        groups = [group(job) for job in jobs]
        if groups == sorted(groups):
            return jobs
        sizes = [groups.count(g) for g in (0, 1, 2)]
        found = ("найденные пропускаю — добор только для Брака" if s.get("parser_recheck_nomatch_only")
                 else "добор для найденных")
        parts = [f"{what} ({n})" for what, n in zip(("новые файлы", "добор для Брака", found), sizes) if n]
        self.log("ПОРЯДОК: " + " → ".join(parts))
        return [job for _g, job in sorted(zip(groups, jobs), key=lambda pair: pair[0])]

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self) -> dict:
        started = time.time()
        if not self._wait_for_database():
            return {"stopped": True}
        jobs = self.collect()
        if not jobs:
            self.log("НЕЧЕГО ПАРСИТЬ")
            return {"total": 0}
        if not self._disk_ok():
            return {"total": len(jobs), "stopped": True}

        http = HttpClient(self.settings, log=self.log, cancel_check=self.stopped,
                          pause_wait=self._hold_while_paused)
        ctx = ParserContext(self.settings, http, self.log, self.activity)
        sites = build_sites(ctx)
        services = build_reverse(ctx, sites)
        self._log_plan(sites, services)
        low_power = bool(self.settings.get("tagger_low_power_mode"))
        workers = 1 if low_power else ctx.setting_int("parser_workers", 3, 1, 8)
        reverse_workers = 1 if low_power else ctx.setting_int("parser_reverse_workers", 2, 1, 4)
        # the stages after the sites mostly wait for the sites (v5.34), so that the files that wait for a site's turn go
        # to it in one request (core/parser/pixels.py); (v5.35.1) reverse search's answers wait for the sites here too
        # (a service's hits looked up, a find asked of the other sites); the reverse-search threads only make the
        # pictures the services are sent.  (v5.39) A waiting file holds a thread and nothing else (its pixel hash is
        # made): with 3 × the file workers ATF's lists carried 3 files of 20 (14 requests of 21 s in 5 minutes, the
        # owner's «ВРЕМЯ») — eight times the file workers fill them: as many requests, more files in each
        after_workers = 1 if low_power else min(48, max(12, 8 * workers))
        site_pool = ThreadPoolExecutor(max_workers=max(2, min(8, len(sites) or 2)), thread_name_prefix="parser-site")
        batch = self._make_batch(ctx, sites, site_pool)
        engine = ParserEngine(ctx, sites, services, site_pool, batch=batch)
        self._engine, self._http = engine, http
        if engine.recheck_new_sites:
            self.log("ДОБОР НОВЫХ САЙТОВ: обработанные раньше файлы проверяются на сайтах, где их ещё не искали")
        counts: Counter = Counter()
        self.counts = counts                # read by pages that show the run (core/parser/jobs.py)
        self._done = 0
        total = len(jobs)
        self.progress(0, total)
        try:
            # reading and writing on ``workers`` threads, the reads first (core/parser/pool.py); the stages after the
            # sites ask the sites again — on threads of their own, so that waiting for a site holds no read or write
            with PriorityPool(workers, "parser-file") as quick_pool, \
                    ThreadPoolExecutor(max_workers=after_workers, thread_name_prefix="parser-after") as after_pool, \
                    ThreadPoolExecutor(max_workers=reverse_workers, thread_name_prefix="parser-reverse") as slow_pool:
                pools = (quick_pool, after_pool, slow_pool)
                deferred = self._pass(engine, jobs, pools, workers, counts, total)
                if deferred:
                    deferred = self._retry_deferred(engine, list(sites) + list(services), deferred,
                                                    pools, workers, counts, total, ctx)
                for _item in deferred:              # counted only now: the retry could still find them
                    counts["deferred"] += 1
                    self._count(total)
        finally:
            self._engine = None
            site_pool.shutdown(wait=True, cancel_futures=True)
            http.close_all()
        summary = dict(counts, total=total, seconds=int(time.time() - started), stopped=self.stopped())
        self._log_summary(summary)
        self._log_switched_off(list(sites) + list(services), sent_to_nomatch=counts.get("nomatch", 0))
        return summary

    def _pass(self, engine: ParserEngine, jobs: list, pools: tuple, workers: int, counts: Counter,
              total: int) -> list:
        """One pass over ``jobs``.  The files are read on ``quick_pool`` (never more than ``OPEN_FILES`` waiting
        for their answer; a read before any write waiting there), asked by the sites' lanes, saved at the first find
        (later finds join the card) on ``quick_pool``, and when every site has answered without a find — the stages
        after the sites on ``after_pool`` (they ask the sites again: the MD5 in the name, rule34's key), then reverse
        search: the picture made on ``slow_pool``, each service asked in its own lane, its answer looked at (the posts
        behind its hits looked up on the sites) and the file's answer (a find asked of the other sites — their tags
        join the card —, the card saved) on ``after_pool`` (v5.35.1: waiting for a site's turn there holds no thread
        of reverse search).  Returns the deferred jobs (not counted yet: the retry may still find them)."""
        quick_pool, after_pool, slow_pool = pools
        events: queue.Queue = queue.Queue()
        fed_all = threading.Event()               # every file is read and in its lanes: no batch waits for more
        exhausted = False                         # every file is taken (some may still be being read)
        lanes = {site.host: _Lane(self, engine, site, events, fed_all) for site in engine.sites}
        rlanes = {service.name: ReverseLane(self, engine, service, events) for service in engine.reverse_services()}
        self._lanes, self._rlanes = list(lanes.values()), list(rlanes.values())
        files = Files(workers)                    # the files' own stages: reading and writing on ``quick_pool``…
        files.unread = len(jobs)
        writes = WorkStage("writes", "Запись", "запись в библиотеку", workers, per_file=2.0)
        after = WorkStage("after", "После сайтов", "проверки после сайтов",          # …and the stages after the sites
                          getattr(after_pool, "_max_workers", workers), per_file=1.0)
        after_share = Share()                     # of the files that left the sites lately, those with no find
        self._files, self._stages = files, (writes, after)
        for lane in self._lanes + self._rlanes:
            lane.start()
        todo = iter(jobs)
        reading = 0                               # files being read (``open_file``)
        in_lanes = 0                              # files some site has not answered yet (the window of the lanes)
        tasks = 0                                 # saves, stages after the sites, reverse steps, journals
        open_files: set = set()                   # files read and not answered yet (in the lanes or past them)
        deferred: list = []
        paused_said = False
        read_share, reverse_share = Share(), Share()    # where the files of the moment go (``estimate``)
        flow = {"unread": len(jobs), "at_sites": 0, "read_share": read_share, "reverse_share": reverse_share,
                "after_share": after_share, "writing": 0}     # writing — files whose writes are not over yet
        self._flow = flow
        told = [time.monotonic()]                 # when the time of the steps was said last (``_say_time``)

        def submit(pool, kind: str, entry, fn, *args, mark: bool = False, counted: bool = True):
            """``counted`` — a task of the stage of its pool (writing, after the sites): no — another's on its threads
            (the answer of reverse search waits for the sites on the threads of the stages after them, v5.35.1)."""
            nonlocal tasks
            tasks += 1
            stage = None if not counted else writes if pool is quick_pool else after if pool is after_pool else None
            if stage is writes:
                entry.writes += 1
            if stage is not None:
                stage.queued()
            future = pool.submit(self._task, entry.job, fn, *args, mark=mark, stage=stage)
            future.add_done_callback(lambda f: events.put((kind, entry, f)))

        def writes_over(entry: "_Open") -> None:
            """The file's writing is over (closed, or gone on to the stages after the sites)."""
            if not entry.writes_over:
                entry.writes_over = True
                flow["writing"] -= 1
                writes.file_over(entry.writes)

        def settle(job: FileJob, result: FileResult) -> None:
            self._tell(job, result)
            if result.status == "deferred" and not self.stopped():
                deferred.append((job, result))
                return
            counts[result.status] += 1
            self._count(total)

        def save(entry: "_Open") -> None:
            """The first find is saved at once; later finds join the card one save after another — only the new ones
            (the card keeps what it has, and a file waiting long for a slow site holds no posts in memory)."""
            if entry.saving or entry.failed or not entry.pending:
                return
            finds, entry.pending = entry.pending, []
            entry.saving = True
            submit(quick_pool, "saved", entry, engine.save_found, entry.job, entry.state, finds, entry.found)

        def go_on(entry: "_Open") -> None:
            """Every lane has answered (and no save is going): the file's next step."""
            nonlocal in_lanes
            if entry.waiting or entry.saving or entry.leaving:
                return
            entry.leaving = True
            in_lanes -= 1                         # past the lanes: a slow reverse search holds up no new file
            flow["at_sites"] = in_lanes
            if entry.found or entry.failed:
                reverse_share.note(False)
                after_share.note(False)
                submit(quick_pool, "closed", entry, engine.close_file, entry.job, entry.state)
            elif self.stopped():
                settle(entry.job, FileResult(entry.job.path, "skip", "остановлено"))
                submit(quick_pool, "closed", entry, engine.close_file, entry.job, entry.state)
            else:
                after_share.note(True)
                writes_over(entry)
                submit(after_pool, "after", entry, engine.after_sites, entry.job, entry.state, mark=True)

        def give_up(entry: "_Open", result: FileResult) -> None:
            """A file leaves reverse search without its answer (the run stopped, a bug): what the sites said is
            kept (the site journal), the file is counted."""
            settle(entry.job, result)
            submit(slow_pool, "closed", entry, engine.abandon, entry.ticket)

        def next_service(entry: "_Open") -> None:
            """The file goes to the next service's lane — or, none left, to its answer."""
            nonlocal tasks
            if self.stopped():
                give_up(entry, FileResult(entry.job.path, "skip", "остановлено"))
                return
            service = engine.reverse_next(entry.ticket)
            while service is not None and service.name not in rlanes:     # not on when the pass began
                service = engine.reverse_next(entry.ticket)
            if service is None:
                submit(after_pool, "found", entry, engine.reverse_finish, entry.ticket, [], counted=False)
                return
            tasks += 1
            rlanes[service.name].put(entry)

        try:
            while True:
                while (reading < workers * 2 and reading + in_lanes < OPEN_FILES and not exhausted
                       and not self.stopped() and not self._pause.is_set()):
                    job = next(todo, None)
                    if job is None:
                        exhausted = True
                        break
                    reading += 1
                    flow["unread"] -= 1
                    files.unread = flow["unread"]
                    files.started()
                    future = quick_pool.submit(self._one, engine.open_file, job, priority=READ)
                    future.add_done_callback(lambda f, job=job: events.put(("opened", job, f)))
                if self.stopped():
                    exhausted = True
                if exhausted and not reading:
                    fed_all.set()
                if self._pause.is_set() != paused_said and not self.stopped():
                    paused_said = self._pause.is_set()
                    self.log("ПАУЗА" if paused_said else "ПРОДОЛЖАЮ")
                if exhausted and not reading and not open_files and not tasks:
                    break
                self._say_time(told)
                try:
                    kind, item, payload = events.get(timeout=0.5)
                except queue.Empty:
                    continue
                if kind == "opened":
                    reading -= 1
                    files.opened()
                    answer = payload.result()
                    if isinstance(answer, FileResult):        # stopped, failed, or a bug in this file
                        read_share.note(False)
                        for lane in lanes.values():
                            lane.share.note(False)
                        settle(item, answer)
                        continue
                    first, sites = answer
                    if isinstance(first, FileResult):         # needs no site: skipped, unreadable, the gallery's tags
                        read_share.note(False)
                        for lane in lanes.values():
                            lane.share.note(False)
                        settle(item, first)
                        continue
                    entry = _Open(item, first, {site.host for site in sites})
                    open_files.add(entry)
                    flow["writing"] += 1
                    in_lanes += 1
                    flow["at_sites"] = in_lanes
                    read_share.note(bool(sites))
                    for host, lane in lanes.items():
                        lane.share.note(host in entry.waiting)
                    for site in sites:
                        lanes[site.host].put(entry)
                    go_on(entry)                              # no site to ask: straight on
                elif kind == "answer":
                    entry, (host, post) = item, payload
                    entry.waiting.discard(host)
                    if post is not None and not entry.failed:
                        entry.pending.append(Match(post, method="md5", verified="md5"))
                        save(entry)
                    go_on(entry)
                elif kind == "saved":
                    tasks -= 1
                    entry, result = item, payload.result()
                    first = not entry.found
                    entry.saving = False
                    if result.status == "tagged":
                        entry.found = True
                    elif first:                               # the first find not written: no more are added to it
                        entry.failed = True
                    if first:
                        settle(entry.job, result)
                    save(entry)                               # finds that came while it was saved
                    go_on(entry)
                elif kind == "after":
                    tasks -= 1
                    after.file_over(1)
                    entry, result = item, payload.result()
                    reverse_share.note(isinstance(result, ReverseTicket))
                    if isinstance(result, ReverseTicket):
                        entry.ticket = result
                        submit(slow_pool, "prepared", entry, engine.reverse_prepare, result)
                        continue
                    open_files.discard(entry)
                    settle(entry.job, result)
                elif kind == "prepared":                      # the picture for the services is made
                    tasks -= 1
                    ready = payload.result()
                    if isinstance(ready, FileResult):         # a bug in making it, or the run stopped
                        give_up(item, ready)
                    elif ready:
                        next_service(item)
                    else:                                     # nothing to search with: the file's answer
                        submit(after_pool, "found", item, engine.reverse_finish, item.ticket, [], counted=False)
                elif kind == "asked":                         # a service answered (in its own lane)
                    tasks -= 1
                    service, outcome = payload
                    if outcome is None:                       # the run stopped before it was asked
                        give_up(item, FileResult(item.job.path, "skip", "остановлено"))
                        continue
                    submit(after_pool, "taken", item, self._take_answer, engine, rlanes.get(service.name), service,
                           item.ticket, outcome, counted=False)
                elif kind == "taken":                         # what the service's answer gives the file
                    tasks -= 1
                    matches = payload.result()
                    if isinstance(matches, FileResult):
                        give_up(item, matches)
                    elif matches:
                        submit(after_pool, "found", item, engine.reverse_finish, item.ticket, matches, counted=False)
                    else:
                        next_service(item)
                elif kind == "found":                         # reverse search is over: the file's answer
                    tasks -= 1
                    open_files.discard(item)
                    settle(item.job, payload.result())
                elif kind == "closed":
                    tasks -= 1
                    writes_over(item)                         # (a reverse file's writing was over before)
                    open_files.discard(item)
        finally:
            self._say_time(told, force=True)
            fed_all.set()
            for lane in list(lanes.values()) + list(rlanes.values()):
                lane.close()
            for lane in list(lanes.values()) + list(rlanes.values()):
                lane.join()
        return deferred

    @staticmethod
    def _take_answer(engine: ParserEngine, lane, service, ticket, outcome):
        """(a thread of the stages after the sites) what a service's answer gives the file — its finds or [] (the next service is asked);
        the service's lane keeps the share of files it did not find (``estimate``)."""
        matches = engine.reverse_take(service, ticket, outcome)
        if lane is not None:
            lane.passed.note(not matches)
        return matches

    def estimate(self) -> tuple[int | None, str]:
        """How long the run still takes (seconds) and the queue that decides it — by every queue's own pace (see
        core/parser/lanes.py ``estimate``); (None, "") while some queue with work has no pace yet."""
        flow = self._flow
        if not flow:
            return None, ""
        return estimate(list(self._lanes), list(self._rlanes), unread=max(0, int(flow.get("unread", 0))),
                        at_sites=max(0, int(flow.get("at_sites", 0))),
                        to_reverse=flow["reverse_share"].fraction(0.0), read_share=flow["read_share"].fraction(1.0),
                        files=self._files, stages=self._stage_work(flow))

    def _stage_work(self, flow: dict) -> tuple:
        """(stage, the files whose part of it is not over) for ``estimate`` and the page: the writing — the files
        open and those still to be read that need a site; the stages after the sites — those waiting there and the
        files at the sites and to be read in the share that had no find lately."""
        if not self._stages:
            return ()
        writes, after = self._stages
        unread = max(0, int(flow.get("unread", 0)))
        coming = unread * flow["read_share"].fraction(1.0)
        to_after = after.waiting + (max(0, int(flow.get("at_sites", 0))) + coming) * flow["after_share"].fraction(0.0)
        return ((writes, max(0, int(flow.get("writing", 0))) + coming), (after, to_after))

    def lanes(self) -> list:
        """The queues of the pass going now, for a page: the files' own (read and written), the sites' (MD5), then
        the reverse-search services' — name, how it asks, files waiting, answered, its pace."""
        files = [self._files.snapshot()] if self._files is not None else []
        flow = self._flow
        stages = [stage.snapshot(to_come) for stage, to_come in self._stage_work(flow)] if flow else []
        return files + stages + [lane.snapshot() for lane in list(self._lanes) + list(self._rlanes)]

    # ── a site switched off for the run, asked again («Проверить снова» on the page «Парсер») ──
    def site_again(self, host: str) -> str:
        """A site switched off for this run (a Cloudflare wall, a refused key) is asked once more, in the background,
        with its cookies and its settings read again — the person has just renewed them.  When it answers it is back
        on for the files read from then on; those that went on without it have no line of it in the site journal, so
        «Добор новых сайтов» asks them later.  "" — the asking has started (or is going on); else why not."""
        engine = self._engine
        if engine is None or self.stopped():
            return "Парсер сейчас не работает"
        site = next((s for s in engine.sites if s.host == clean_host(host)), None)
        if site is None:
            return "Такого сайта в этом запуске нет"
        with self._again_lock:
            if not site.disabled_reason:
                return "Сайт и так работает"
            if site.again_note == ASKING_AGAIN:
                return ""
            site.again_note = ASKING_AGAIN
        threading.Thread(target=self._site_again, args=(engine, site), name=f"parser-again-{site.host}",
                         daemon=True).start()
        return ""

    def _site_again(self, engine: ParserEngine, site) -> None:
        from core.parser.sites import site_rows
        from core.settings import load_settings
        try:
            fresh = site_rows(load_settings()).get(site.host)
        except Exception as exc:  # the settings file: the site is asked with what the run started with
            self.log(f"  {site.label}: настройки сайта не перечитаны ({type(exc).__name__}: {exc})")
            fresh = None
        if fresh:
            site.cfg = dict(fresh)                # a key written on «Сайты и ключи» meanwhile
        if self._http is not None:
            self._http.renew_cookies(site.host)   # the cookies Companion wrote after the browser's check
        self.log(f"  {site.label}: проверяю снова — cookies и настройки перечитаны")
        try:
            site.search_md5("0" * 32)             # any real question: a wall or a refusal shows itself
        except Cancelled:
            site.again_note = "запуск остановлен — сайт не проверен"
            return
        except (SiteUnavailable, HttpFailure) as exc:
            reason = short(getattr(exc, "detail", None) or exc, 120)
            site.again_note = f"не вышло: {reason}"
            self.log(f"  {site.label}: снова не отвечает ({reason}) — остаётся отключённым")
            return
        except Exception as exc:  # a bug in the asking: the site stays off and the page says why
            site.again_note = f"проверка сломалась: {type(exc).__name__}: {exc}"
            self.log(f"  {site.label}: проверка снова сломалась: {type(exc).__name__}: {exc}")
            return
        site.enable_again()
        site.again_note = ""
        self.log(f"  ✔ {site.label}: снова включён — файлы, взятые с этой минуты, спрашиваются и на нём; "
                 "прошедшие без него проверит «Добор новых сайтов» в следующем запуске")
        if engine.batch is not None:
            try:
                engine.batch.recheck(site)        # walled off at the start, its 2/2 check had failed
            except Cancelled:
                return
            except Exception as exc:  # without batches the site is asked one file at a time, as before
                self.log(f"  {site.label}: пакетные MD5-запросы не проверены ({type(exc).__name__}: {exc})")

    def _task(self, job: FileJob, fn, *args, mark: bool = False, stage: WorkStage | None = None):
        """A step of a file's work in a pool thread (``mark``: the file is in work again — the stages after the
        sites; ``stage``: whose task it is — it counts the task done and its time); a bug in it is the file's error,
        never the run's end."""
        started = time.perf_counter()
        if mark:
            self.current_file(str(job.path))
        try:
            return fn(*args)
        except Cancelled:
            return FileResult(job.path, "skip", "остановлено")
        except Exception as exc:  # a bug in one file must not kill the run, but must be visible
            self.log(f"[{job.index}] ОШИБКА {job.path.name}: {type(exc).__name__}: {exc}")
            self.log("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:])
            return FileResult(job.path, "error", f"{type(exc).__name__}: {exc}")
        finally:
            if stage is not None:
                stage.finished(time.perf_counter() - started)

    def _say_time(self, told: list, *, force: bool = False) -> None:
        """Every ``TIME_EVERY`` seconds (and at the end of a pass): where the time of the files went, step by step
        (core/work_timing.py) — a slow run says what is slow on this computer."""
        now = time.monotonic()
        if not force and now - told[0] < TIME_EVERY:
            return
        minutes = max(1.0, (now - told[0]) / 60.0)
        told[0] = now
        line = work_timing.say(work_timing.STEPS.take(), minutes, work_timing.STEPS.notes())
        if line:
            self.log(line)

    def _count(self, total: int) -> None:
        self._done += 1
        self.progress(self._done, total)

    def _tell(self, job: FileJob, result: FileResult) -> None:
        """A file's answer to whoever shows the run; a fault there never stops the run."""
        try:
            self.file_result(str(job.path), result)
        except Exception as exc:  # the page's bookkeeping, not the parser's work
            self.log(f"  итог файла для страницы не записан: {type(exc).__name__}: {exc}")

    def _retry_deferred(self, engine: ParserEngine, sources: list, deferred: list, pools: tuple,
                        workers: int, counts: Counter, total: int, ctx: ParserContext) -> list:
        """Files deferred because a site or service paused after network errors get one more try in
        this run, once the pauses are over — instead of waiting for the next run.  Waits at most
        ``parser_retry_deferred_minutes``; a file held by a long limit (SauceNAO daily quota) stays
        deferred."""
        limit = ctx.setting_int("parser_retry_deferred_minutes", 15, 0, 120) * 60
        if not limit or self.stopped():
            return deferred
        horizon = time.time() + limit
        retry, keep = [], []
        for item in deferred:
            (retry if not item[1].retry_at or item[1].retry_at <= horizon else keep).append(item)
        if not retry:
            return deferred
        deadline = time.monotonic() + limit
        waiting = [src for src in sources if src.paused()]
        if waiting:
            self.log(f"ПОВТОР ОТЛОЖЕННЫХ: {len(retry)} файлов — жду конца паузы "
                     f"({', '.join(src._title() for src in waiting)}), не дольше {limit // 60} мин")
        while any(src.paused() for src in sources) and time.monotonic() < deadline and not self.stopped():
            time.sleep(1.0)
        if self.stopped() or any(src.paused() for src in sources):
            self.log("ПОВТОР ОТЛОЖЕННЫХ отменён: паузы не кончились — эти файлы проверятся в следующий запуск")
            return deferred
        self.log(f"ПОВТОР ОТЛОЖЕННЫХ: проверяю ещё раз {len(retry)} файлов")
        still = self._pass(engine, [job for job, _r in retry], pools, workers, counts, total)
        return keep + still

    def _make_batch(self, ctx: ParserContext, sites: list, pool) -> Md5Batch | None:
        """Batched exact-MD5 questions (see core/parser/batch.py), when switched on and useful."""
        if not ctx.flag("enable_md5_lookup", True):
            return None
        if not ctx.flag("parser_md5_batch_enabled", ctx.flag("tagger_md5_batch_enabled", True)):
            self.log("ПАКЕТНЫЕ MD5-ЗАПРОСЫ: выключены в настройках")
            return None
        batch = Md5Batch(ctx, sites, size=ctx.setting_int("parser_md5_batch_size", 40, 2, 100))
        if not batch.sites:
            return None
        self.log("ПАКЕТНЫЕ MD5-ЗАПРОСЫ: проверяю, какие сайты их понимают")
        try:
            batch.start(pool)
        except Cancelled:
            return None
        return batch

    def _one(self, stage, item, job: FileJob | None = None):
        """``stage`` (engine.open_file) for one file, in a pool thread."""
        job = job or item
        self._hold_while_paused()
        if self.stopped():
            return FileResult(job.path, "skip", "остановлено")
        self.current_file(str(job.path))
        try:
            return stage(item)
        except Cancelled:
            return FileResult(job.path, "skip", "остановлено")
        except Exception as exc:  # a bug in one file must not kill the run, but must be visible
            self.log(f"[{job.index}] ОШИБКА {job.path.name}: {type(exc).__name__}: {exc}")
            self.log("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2000:])
            try:
                from core.tagger.results import append_error_log
                append_error_log(f"PARSER {job.path}: {traceback.format_exc()}")
            except Exception:
                pass
            return FileResult(job.path, "error", f"{type(exc).__name__}: {exc}")

    def _wait_for_database(self) -> bool:
        from core.database.connection import writes_blocked, writes_blocked_reason
        waited = 0.0
        while writes_blocked():
            if waited == 0:
                self.log(f"ЖДУ БАЗУ: идёт фоновая проверка SQLite ({writes_blocked_reason()})")
            if self.stopped() or waited > 600:
                self.log("ПАРСЕР НЕ ЗАПУЩЕН: база в режиме только чтения")
                return False
            time.sleep(1.0)
            waited += 1.0
        return True

    def _disk_ok(self) -> bool:
        try:
            from core.preflight import format_bytes, output_disk_info
            disk = output_disk_info(self.settings)
        except Exception as exc:
            self.log(f"проверка места на диске не удалась: {exc}")
            return True
        if int(disk.get("free", 0) or 0) < int(disk.get("reserve", 0) or 0):
            self.log(f"СТОП: мало места на диске — свободно {format_bytes(disk.get('free', 0))}, "
                     f"нужен резерв {format_bytes(disk.get('reserve', 0))}")
            return False
        return True

    def _log_plan(self, sites: list, services: list) -> None:
        self.log("САЙТЫ (MD5): " + (" → ".join(s.label for s in sites) or "нет включённых сайтов"))
        enabled = [s.name for s in services if s.enabled()]
        self.log("ОБРАТНЫЙ ПОИСК: " + (" → ".join(enabled) or "выключен"))
        if self.settings.get("enable_saucenao", True) and not str(self.settings.get("saucenao_api_key") or "").strip():
            self.log("  SauceNAO выключен: не указан API-ключ")

    def _log_summary(self, s: dict) -> None:
        self.log(
            f"ГОТОВО за {s['seconds'] // 60} мин {s['seconds'] % 60} с: найдено {s.get('tagged', 0)}, "
            f"в брак {s.get('nomatch', 0)}, отложено {s.get('deferred', 0)}, пропущено {s.get('skip', 0)}, "
            f"ошибок {s.get('error', 0)} из {s['total']}" + (" (остановлено)" if s.get("stopped") else "")
        )
        if s.get("deferred"):
            self.log("  Отложенные файлы не записаны в Брак — запусти парсер ещё раз позже, они проверятся снова. "
                     "Если какой-то сайт или сервис не работает постоянно, выключи его галочкой, "
                     "иначе файлы так и будут откладываться.")


    def _log_switched_off(self, sources: list, *, sent_to_nomatch: int) -> None:
        off = [s for s in sources if getattr(s, "disabled_reason", "") or getattr(s, "ever_disabled", "")]
        for src in off:
            if src.disabled_reason:
                self.log(f"⚠ {src._title()} был отключён в этом запуске: {src.disabled_reason}")
            else:
                self.log(f"⚠ {src._title()} был отключён часть запуска ({src.ever_disabled}) — потом снова включён")
        if off and sent_to_nomatch:
            self.log("  Файлы, ушедшие в Брак в этом запуске, на отключённых сайтах не проверялись: "
                     "исправь причину и запусти «Брак → Повторный поиск».")


def _load_retry_manifest(root: Path) -> dict:
    """NO_MATCH retry stages hardlinks in a temp folder; map them back to the real rows."""
    path = root / RETRY_MANIFEST
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for item in data.get("items") or []:
        if isinstance(item, dict) and item.get("queue_file"):
            out[str(item["queue_file"])] = item
            out[Path(str(item["queue_file"])).name] = item
    return out
