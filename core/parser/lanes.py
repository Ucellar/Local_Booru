"""The queues of a parser run (core/parser/runner.py): every MD5 site and every reverse-search service has its own —
a «lane», one thread that goes through its files at its own pace and never waits for another.

* ``SiteLane`` — one MD5 site: the files in the order they come, one by one or many in one request when the site
  takes batches (core/parser/batch.py), several at once when its API allows it (rule34.xxx).
* ``ReverseLane`` — one reverse-search service (IQDB, SauceNAO…): a file the service does not find goes on to the next
  service's lane, so a service that waits (SauceNAO keeps to its account's limits) holds up no other — IQDB goes on
  with the next files meanwhile.
* ``Files`` — the files' own stage: read (their MD5, what the library knows of them) and written (finds saved, files
  closed) on the run's workers.  When the library is big or the disk slow it is the slowest: the sites wait for files.
* ``Pace`` — how fast a lane answers while it has work (the answers of the last minutes over the time it was busy),
  and ``estimate`` — how long the run still takes: every stage's work (what waits for it and what will still come to
  it) at its own pace; the stages work side by side, so the slowest of them decides.

Every file put into a lane gets exactly one answer through ``events`` (after a stop — without asking): the runner's
loop keeps the account of every file.
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from core.parser.http import Cancelled
from core.parser.models import ReverseOutcome

OFF_HOW = "выключен"         # how a lane asks when its site is switched off for the run (the page shows why)
ASKING_AGAIN = "проверяю…"   # a switched-off site is being asked again («Проверить снова»)
RECENT = 400                 # files remembered for the shares of the estimate (where the files of the moment go)


class Pace:
    """A lane's answers over the last ``WINDOW`` seconds and the time it was busy then: answers per second of work.
    The time it waited for files with nothing to do is not counted — a lane that keeps up with a trickle of files
    is not slow."""

    WINDOW = 300.0
    MIN_ANSWERS = 3
    MIN_BUSY = 10.0
    LONG_BUSY = 120.0        # a slow lane: one or two answers in two minutes of work tell its pace too

    def __init__(self):
        self._lock = threading.Lock()
        self._answers: deque = deque()            # (when, how many)
        self._idle: deque = deque()               # (from, to) of the times without work
        self._idle_since: float | None = None     # waiting for work now (since when); None — busy
        self.started = time.monotonic()

    def idle(self, now: float | None = None) -> None:
        with self._lock:
            if self._idle_since is None:
                self._idle_since = time.monotonic() if now is None else now

    def busy(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._idle_since is not None:
                self._idle.append((self._idle_since, now))
                self._idle_since = None
            self._trim(now)

    def answered(self, count: int = 1, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._answers.append((now, int(count)))
            self._trim(now)

    def _trim(self, now: float) -> None:
        start = now - self.WINDOW
        while self._answers and self._answers[0][0] < start:
            self._answers.popleft()
        while self._idle and self._idle[0][1] < start:
            self._idle.popleft()

    def per_second(self, now: float | None = None) -> float:
        """Answers per second of work; 0.0 — too little to tell yet."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._trim(now)
            start = max(now - self.WINDOW, self.started)
            answers = sum(n for _t, n in self._answers)
            idle = sum(max(0.0, min(b, now) - max(a, start)) for a, b in self._idle)
            if self._idle_since is not None:
                idle += max(0.0, now - max(self._idle_since, start))
        busy = (now - start) - idle
        if answers <= 0 or busy < self.MIN_BUSY or (answers < self.MIN_ANSWERS and busy < self.LONG_BUSY):
            return 0.0
        return answers / busy


class Share:
    """Of the last ``RECENT`` files, how many did something (went into a lane, went on to reverse search)."""

    def __init__(self, size: int = RECENT):
        self._seen: deque = deque(maxlen=size)
        self._lock = threading.Lock()

    def note(self, yes: bool) -> None:
        with self._lock:
            self._seen.append(1 if yes else 0)

    def fraction(self, default: float = 1.0) -> float:
        with self._lock:
            return sum(self._seen) / len(self._seen) if self._seen else default


class Files:
    """The files' first stage in a pass (core/parser/runner.py): every file is read on the run's workers (its MD5, what
    the library knows of it) before any site is asked.  Its pace — files read per second of the time some file was
    being read (not the time the window of the lanes was full: then the sites are the slow ones).  The reads go
    before the writes on those workers (core/parser/pool.py)."""

    kind = "files"
    name = "чтение файлов"

    def __init__(self, workers: int):
        self.workers = max(1, int(workers))
        self.pace = Pace()
        self.reading = 0          # files being read now
        self.read = 0             # files read in this pass
        self.unread = 0           # files not taken yet (the runner keeps it)

    def started(self) -> None:
        """A file is given to the workers to be read."""
        if self.reading == 0:
            self.pace.busy()
        self.reading += 1

    def opened(self) -> None:
        """A file's reading is over (read, skipped, or failed)."""
        self.reading = max(0, self.reading - 1)
        self.read += 1
        self.pace.answered()
        if self.reading == 0:
            self.pace.idle()

    def snapshot(self) -> dict:
        rate = self.pace.per_second()
        waiting = max(0, int(self.unread))
        how = f"{self.workers} {_threads(self.workers)}" + (" (режим слабого компьютера)" if self.workers == 1 else "")
        return {"site": "Чтение", "host": "files", "kind": self.kind, "stage": "read", "how": how,
                "waiting": waiting, "answered": self.read, "asking": "", "reason": "", "again": "",
                "per_minute": round(rate * 60, 1), "eta": int(waiting / rate) if waiting and rate > 0 else None,
                "reading": self.reading}


class WorkStage:
    """A stage of the files' own work after the reading, on its own workers: «запись в библиотеку» — what the sites
    found written (a save, a later find joining the card, the site journal), on the reading's workers after the reads;
    «после сайтов» — a file no site had: the MD5 in its name, rule34's image key, the same picture in the gallery —
    questions to the sites again, on workers of their own so that the reading and the writing never wait for them.

    Tasks given and done; its pace — tasks done per second of the time some were waiting; ``work`` — what waits and
    what the files still to come will bring: as many tasks per file as the files done with this stage brought (before
    ``SAMPLE`` of them — ``per_file``), less what those still open have been given already."""

    kind = "files"
    SAMPLE = 20

    def __init__(self, stage: str, title: str, name: str, workers: int, per_file: float):
        self.stage, self.title, self.name = stage, title, name
        self.workers = max(1, int(workers))
        self.default_per_file = float(per_file)
        self.pace = Pace()
        self.pace.idle()                    # nothing to do before the first task
        self._lock = threading.Lock()
        self.waiting = 0                    # given to the workers, not done yet
        self.done = 0
        self.seconds = 0.0                  # how long the done ones took
        self.files = 0                      # files whose part of this stage is over…
        self.files_tasks = 0                # …and the tasks they brought
        self.open_tasks = 0                 # tasks given for the files whose part is not over yet

    def queued(self) -> None:
        with self._lock:
            if self.waiting == 0:
                self.pace.busy()
            self.waiting += 1
            self.open_tasks += 1

    def finished(self, seconds: float = 0.0) -> None:
        with self._lock:
            self.waiting = max(0, self.waiting - 1)
            self.done += 1
            self.seconds += max(0.0, float(seconds))
            self.pace.answered()
            if self.waiting == 0:
                self.pace.idle()

    def file_over(self, tasks: int) -> None:
        """A file's part of this stage is over: it brought ``tasks``."""
        with self._lock:
            self.files += 1
            self.files_tasks += int(tasks)
            self.open_tasks = max(0, self.open_tasks - int(tasks))

    def per_file(self) -> float:
        with self._lock:
            return self.files_tasks / self.files if self.files >= self.SAMPLE else self.default_per_file

    def work(self, files_to_come: float) -> float:
        """Tasks still to do: those waiting, and what ``files_to_come`` (the files whose part is not over, open or
        not read yet) will still bring."""
        per_file = self.per_file()
        with self._lock:
            return self.waiting + max(0.0, per_file * max(0.0, files_to_come) - self.open_tasks)

    def snapshot(self, files_to_come: float = 0.0) -> dict:
        rate = self.pace.per_second()
        work = self.work(files_to_come)
        average = self.seconds / self.done if self.done else 0.0
        return {"site": self.title, "host": self.stage, "kind": self.kind, "stage": self.stage,
                "how": f"{self.workers} {_threads(self.workers)}", "waiting": self.waiting, "answered": self.done,
                "asking": "", "reason": "", "again": "", "per_minute": round(rate * 60, 1),
                "eta": int(work / rate) if work >= 1 and rate > 0 else None, "reading": 0,
                "average": round(average, 2)}


def _threads(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "поток"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "потока"
    return "потоков"


class Open:
    """A file asked by the lanes: which sites have not answered yet, what they found, what is saved; after them —
    its reverse search (``ticket``)."""

    __slots__ = ("job", "state", "waiting", "pending", "found", "saving", "failed", "leaving", "ticket", "writes",
                 "writes_over")

    def __init__(self, job, state, waiting: set):
        self.job, self.state, self.waiting = job, state, set(waiting)
        self.writes = 0               # its tasks of «запись в библиотеку» (saves, the site journal)
        self.writes_over = False      # …and none will come any more
        self.pending: list = []       # finds not written yet
        self.found = False            # the card is in the library (the first find was written)
        self.saving = False
        self.failed = False           # the first find was not written: no more finds are added
        self.leaving = False          # every lane answered: the journal / the stages after the sites are on
        self.ticket = None            # core.parser.engine.ReverseTicket while it is in reverse search


class SiteLane:
    """One MD5 site's queue: the files in the order they come, asked one by one, or many in one request when the
    site takes batches.  A slow site keeps its own backlog and holds up no other site.  Every file put here gets
    exactly one answer (``events``: "answer", the file, (host, post or None)) — after a stop without asking.
    A site that takes several requests at once (``site.md5_parallel``: rule34.xxx's API) is asked about that many
    files at a time; its gate in core/parser/http.py goes one by one again after an HTTP 429."""

    LINGER = 0.4             # a batch waits for more files while they keep coming (this long after the last one)…
    LINGER_MAX = 3.0         # …but never longer than this in all
    kind = "site"

    def __init__(self, runner, engine, site, events: queue.Queue, fed_all: threading.Event):
        self.runner, self.engine, self.site, self.events, self.fed_all = runner, engine, site, events, fed_all
        self.batch = engine.batch if engine.batch is not None and site in engine.batch.sites else None
        self.parallel = max(1, min(12, int(getattr(site, "md5_parallel", 1) or 1)))
        self._queue: deque = deque()
        self._cond = threading.Condition()
        self._closed = False
        self._count_lock = threading.Lock()
        self.asking = ""          # the file asked now (for a page)
        self.answered = 0
        self.pace = Pace()
        self.share = Share()      # of the files read lately, how many need this site
        self._thread = threading.Thread(target=self._run, name=f"parser-lane-{site.host}", daemon=True)

    @property
    def name(self) -> str:
        return self.site.label

    def start(self) -> None:
        self._thread.start()

    def put(self, entry: Open) -> None:
        with self._cond:
            if not self._closed:
                self._queue.append(entry)
                self._cond.notify()
                return
        self._lost(entry)

    def _lost(self, entry: Open) -> None:
        """A file this lane will not ask about gets its answer now.  Unless the run stopped, the site's silence is a
        problem of the file: it waits for the next run instead of going to Брак."""
        if not self.runner.stopped():
            entry.state.problem(self.site, f"{self.site.label}: очередь сайта остановилась")
        self.events.put(("answer", entry, (self.site.host, None)))

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify()

    def join(self) -> None:
        self._thread.join(timeout=30)

    def waiting(self) -> int:
        with self._cond:
            return len(self._queue)

    def snapshot(self) -> dict:
        waiting = self.waiting()
        site = self.site
        if site.disabled_reason:
            how = OFF_HOW
        elif site.paused():
            how = "пауза"
        elif self.batch is not None and self.batch.on(site):
            how = f"пакетами по {self.batch.chunk(site)}"
        elif self.parallel > 1:
            how = f"по одному, до {self.parallel} сразу"
        else:
            how = "по одному"
        rate = self.pace.per_second()
        return {"site": site.label, "host": site.host, "kind": self.kind, "how": how, "waiting": waiting,
                "answered": self.answered, "asking": self.asking, "reason": site.disabled_reason,
                "again": site.again_note, "per_minute": round(rate * 60, 1),
                "eta": int(waiting / rate) if waiting and rate > 0 else None}

    def _batched(self) -> bool:
        return self.batch is not None and self.batch.on(self.site)

    def _take(self) -> list:
        with self._cond:
            if not self._queue:
                self.pace.idle()
            while not self._queue:
                if self._closed or self.runner.stopped():
                    return []
                self._cond.wait(0.5)
            self.pace.busy()
            if not self._batched():
                return [self._queue.popleft()]
            chunk = self.batch.chunk(self.site)
            started = time.monotonic()
            deadline = started + self.LINGER
            seen = len(self._queue)
            while (len(self._queue) < chunk and not self.fed_all.is_set() and not self._closed
                   and not self.runner.stopped()):
                now = time.monotonic()
                if len(self._queue) > seen:          # still coming: wait for the rest of the batch
                    seen = len(self._queue)
                    deadline = min(now + self.LINGER, started + self.LINGER_MAX)
                if now >= deadline:
                    break
                self._cond.wait(deadline - now)
            return [self._queue.popleft() for _ in range(min(chunk, len(self._queue)))]

    def _answer(self, entry: Open, stopped: bool) -> bool:
        """Ask the site about one file and put its answer; True when the run was stopped meanwhile."""
        post = None
        if not stopped and not self.runner.stopped():
            self.asking = str(entry.job.path)
            try:
                post = self.engine.ask(self.site, entry.state.md5, entry.state)
            except Cancelled:
                stopped = True
            except Exception as exc:  # a bug in one answer: the file waits for the next run, never Брак
                entry.state.problem(self.site, f"{self.site.label}: {type(exc).__name__}: {exc}")
                self.runner.log(f"  {self.site.label}: ошибка ответа о {entry.job.path.name}: "
                                f"{type(exc).__name__}: {exc}")
            with self._count_lock:
                self.answered += 1
            self.pace.answered()
        self.events.put(("answer", entry, (self.site.host, post)))
        return stopped

    def _run(self) -> None:
        taken: list = []
        workers = ThreadPoolExecutor(max_workers=self.parallel, thread_name_prefix=f"parser-lane-{self.site.host}") \
            if self.parallel > 1 else None
        places = threading.Semaphore(self.parallel)
        halted = threading.Event()               # a worker saw the stop: the files not asked yet are answered at once

        def answer_in_worker(entry: Open) -> None:
            try:
                if self._answer(entry, halted.is_set()):
                    halted.set()
            finally:
                places.release()

        try:
            while True:
                taken = self._take()
                if not taken:
                    return
                self.runner._hold_while_paused()
                stopped = self.runner.stopped() or halted.is_set()
                if not stopped and len(taken) > 1 and self._batched() and self.site.ready():
                    try:
                        self.batch.ask_site(self.site, [entry.state.md5 for entry in taken])
                    except Cancelled:
                        stopped = True
                    except Exception as exc:  # a bug in the batch: these files are asked one by one
                        self.runner.log(f"  {self.site.label}: пакетный запрос сломался ({type(exc).__name__}: {exc})"
                                        " — спрошу по одному")
                while taken:
                    stopped = stopped or halted.is_set()
                    if workers is None or stopped:
                        stopped = self._answer(taken.pop(0), stopped)
                        continue
                    while not places.acquire(timeout=0.5):       # a place among the requests at once
                        if self.runner.stopped():
                            break
                    else:
                        entry = taken.pop(0)
                        try:
                            workers.submit(answer_in_worker, entry)
                        except RuntimeError:                     # the pool is shutting down: answer here
                            places.release()
                            stopped = self._answer(entry, True)
                        continue
                    stopped = True
                self.asking = ""
        finally:
            if workers is not None:
                workers.shutdown(wait=True)      # the files being asked get their answers first
            # nobody waits for this lane forever: what it still holds is answered without asking
            with self._cond:
                rest = list(self._queue)
                self._queue.clear()
                self._closed = True
            for entry in taken + rest:
                self._lost(entry)
            self.asking = ""


class ReverseLane:
    """One reverse-search service's queue (IQDB, SauceNAO…): the files in the order they come, asked one by one at
    the service's own pace (its limits are kept by core/http_rate_limiter.py).  Only the service's own request is made
    here (``engine.reverse_ask``); what its answer gives — the posts behind its hits — is looked at by the runner's
    workers, so the next file is asked meanwhile.  Every file put here gets exactly one answer (``events``: "asked",
    the file, (service, outcome — None when the run stopped))."""

    kind = "reverse"

    def __init__(self, runner, engine, service, events: queue.Queue):
        self.runner, self.engine, self.service, self.events = runner, engine, service, events
        self._queue: deque = deque()
        self._cond = threading.Condition()
        self._closed = False
        self.asking = ""
        self.answered = 0
        self.pace = Pace()
        self.passed = Share()     # of the files it answered lately, how many it did not find (they go on)
        self._thread = threading.Thread(target=self._run, name=f"parser-reverse-{service.name}", daemon=True)

    @property
    def name(self) -> str:
        return self.service.name

    def start(self) -> None:
        self._thread.start()

    def put(self, entry: Open) -> None:
        with self._cond:
            if not self._closed:
                self._queue.append(entry)
                self._cond.notify()
                return
        self.events.put(("asked", entry, (self.service, None)))

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify()

    def join(self) -> None:
        self._thread.join(timeout=30)

    def waiting(self) -> int:
        with self._cond:
            return len(self._queue)

    def snapshot(self) -> dict:
        waiting = self.waiting()
        service = self.service
        if service.disabled_reason:
            how = OFF_HOW
        elif service.paused():
            how = "пауза"
        else:
            how = service.pace_note() if hasattr(service, "pace_note") else "по одному"
        rate = self.pace.per_second()
        return {"site": service.name, "host": "reverse:" + service.name.lower().replace(" ", "-"), "kind": self.kind,
                "how": how, "waiting": waiting, "answered": self.answered, "asking": self.asking,
                "reason": service.disabled_reason, "again": "", "per_minute": round(rate * 60, 1),
                "eta": int(waiting / rate) if waiting and rate > 0 else None}

    def _take(self):
        with self._cond:
            if not self._queue:
                self.pace.idle()
            while not self._queue:
                if self._closed or self.runner.stopped():
                    return None
                self._cond.wait(0.5)
            self.pace.busy()
            return self._queue.popleft()

    def _run(self) -> None:
        entry = None
        try:
            while True:
                entry = self._take()
                if entry is None:
                    return
                self.runner._hold_while_paused()
                outcome = None
                if not self.runner.stopped():
                    self.asking = str(entry.job.path)
                    try:
                        outcome = self.engine.reverse_ask(self.service, entry.ticket)
                    except Cancelled:
                        outcome = None
                    except Exception as exc:  # a bug in one answer: a failure of the service for this file
                        self.runner.log(f"  {self.service.name}: ошибка поиска {entry.job.path.name}: "
                                        f"{type(exc).__name__}: {exc}")
                        outcome = ReverseOutcome("failed", detail=f"{type(exc).__name__}: {exc}")
                    if outcome is not None:
                        self.answered += 1
                        self.pace.answered()
                    self.asking = ""
                self.events.put(("asked", entry, (self.service, outcome)))
                entry = None
        finally:
            # nobody waits for this lane forever: what it still holds is answered without asking
            with self._cond:
                rest = list(self._queue)
                self._queue.clear()
                self._closed = True
            for left in ([entry] if entry is not None else []) + rest:
                self.events.put(("asked", left, (self.service, None)))
            self.asking = ""


def estimate(site_lanes: list, reverse_lanes: list, *, unread: int, at_sites: int, to_reverse: float,
             read_share: float, files: "Files | None" = None, stages: tuple = (),
             now: float | None = None) -> tuple[int | None, str]:
    """(seconds the run still takes, the stage that decides) — (None, the stage that has work and no pace yet).

    ``unread`` — files not read yet, ``at_sites`` — files some site has not answered yet, ``to_reverse`` — of the
    files that left the sites lately, the share that went on to reverse search, ``read_share`` — of the files read
    lately, the share that needed a site, ``files`` — the reading, ``stages`` — (WorkStage, the files whose part of it
    is not over): the writing, the stages after the sites.  The reading's work: the unread files; a stage's: what
    waits and what its files will still bring; an MD5 site's: what waits for it and its share of the unread files;
    reverse search gets the files still at the sites and to be read in the share that went on to it lately, each
    service those its predecessor did not find.  The stages work side by side: the run ends with the slowest.  One
    with nothing waiting and no pace yet keeps up with what comes to it — it holds nothing up and is not waited for
    (a fast site that gets a file now and then never had the ten seconds of work in five minutes a pace needs, and a
    run had no estimate at all)."""
    now = time.monotonic() if now is None else now
    finish, slowest = 0.0, ""
    if files is not None and unread >= 1:
        rate = files.pace.per_second(now)
        if rate <= 0:
            return None, files.name
        finish, slowest = unread / rate, files.name
    for stage, to_come in stages:
        work = stage.work(to_come)
        if work < 1:
            continue
        rate = stage.pace.per_second(now)
        if rate > 0:
            seconds = work / rate
        elif stage.waiting == 0:
            continue
        else:
            return None, stage.name
        if seconds > finish:
            finish, slowest = seconds, stage.name

    def lane_time(lane, work: float):
        """Seconds of ``lane``'s work at its pace; 0 — nothing to do or keeping up; None — not known yet."""
        if work < 1:
            return 0.0
        rate = lane.pace.per_second(now)
        if rate > 0:
            return work / rate
        return 0.0 if lane.waiting() == 0 else None

    for lane in site_lanes:
        seconds = lane_time(lane, lane.waiting() + unread * lane.share.fraction(1.0))
        if seconds is None:
            return None, lane.name
        if seconds > finish:
            finish, slowest = seconds, lane.name
    coming = (at_sites + unread * read_share) * to_reverse
    for lane in reverse_lanes:
        work = lane.waiting() + coming
        seconds = lane_time(lane, work)
        if seconds is None:
            return None, lane.name
        if seconds > finish:
            finish, slowest = seconds, lane.name
        coming = work * lane.passed.fraction(1.0)
    return int(finish), slowest
