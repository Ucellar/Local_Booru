"""Subscription checks for the whole program: one at a time, and their state for the pages.

Whoever checks subscriptions — the web page «Подписки» (a check now, «Скачать тег»), the old Qt page,
the automatic check — goes through :func:`claim`: two checks at once would download the same posts
and fight over the list of found posts.  :class:`SubscriptionJob` checks one or several
subscriptions in a thread and keeps what a page polls: which subscription is going, files
downloaded, the journal (numbered lines) and the question about a large download (the check waits
for the answer; nobody answers in :data:`PLAN_WAIT_SECONDS` — the download is not done this time).

After «Остановить» the automatic check rests for :data:`AUTO_PAUSE_SECONDS`: the subscriptions it
did not get to are still due, and a minute later it would start them again.

(v5.44.120) «Загрузки» (``webui/pages/downloads.py``): «Пауза» — the check waits before its next file (the engine asks
its stop flag between files and between sites; the file on its way comes); «Пропустить подписку» — the subscription
going stops as at «Остановить» (its found posts wait in its list), is noted as checked now (its time comes again after
its interval, not a minute later) and the check goes on with the next one.  Every check that ends is told to
:data:`ENDED` (``core/download_history``: «Готово»).
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque

LOG_KEEP = 3000                  # lines a page can still fetch
PLAN_WAIT_SECONDS = 30 * 60
AUTO_PAUSE_SECONDS = 60 * 60
FILES_KEPT = 200_000             # (v5.44.141) files of a run kept for what comes after it (favorites → the parser)
ENDED: list = []                 # (v5.44.120) fn(job) when a check has ended (never in its way)
_LOCK = threading.Lock()
_OWNER = ""                      # who checks subscriptions now ("" — nobody)
_CURRENT: "SubscriptionJob | None" = None
_LAST: "SubscriptionJob | None" = None
_SEQ = 0
_AUTO_PAUSED_UNTIL = 0.0
_EXIT_HOOKED = False


class SubscriptionsBusy(RuntimeError):
    """Subscriptions are already being checked (``owner`` says by whom), or the program is closing."""

    def __init__(self, owner: str, message: str = ""):
        from core.parser.jobs import MAINTENANCE_OWNER
        maintenance = owner.startswith(MAINTENANCE_OWNER.split("«")[0])
        super().__init__(message or (f"Сейчас идёт {owner} (Настройки → Проверка и починка → Обслуживание)" if maintenance
                                     else f"Подписки уже проверяются ({owner})"))
        self.owner = owner


def claim(owner: str) -> bool:
    """Take the subscriptions for ``owner``; False when somebody else has them."""
    global _OWNER
    with _LOCK:
        if _OWNER and _OWNER != owner:
            return False
        _OWNER = owner
        return True


def release(owner: str) -> None:
    global _OWNER
    with _LOCK:
        if _OWNER == owner:
            _OWNER = ""


def owner() -> str:
    with _LOCK:
        return _OWNER


def is_running() -> bool:
    return bool(owner())


def current() -> "SubscriptionJob | None":
    """The check in progress, else the last finished one (its result stays on the page)."""
    with _LOCK:
        return _CURRENT or _LAST


def running_ids() -> set:
    """Ids of the subscriptions the check in progress has taken (deleting one of them, or
    resetting its progress, would be undone or confused by the check's end)."""
    with _LOCK:
        job = _CURRENT
    if job is None or job.finished_at:
        return set()
    return {str(s.get("id")) for s in job.subs if s.get("id")}


def auto_paused_until() -> float:
    with _LOCK:
        return _AUTO_PAUSED_UNTIL if _AUTO_PAUSED_UNTIL > time.time() else 0.0


def resume_auto() -> None:
    global _AUTO_PAUSED_UNTIL
    with _LOCK:
        _AUTO_PAUSED_UNTIL = 0.0


class SubscriptionJob:
    """Subscriptions checked one after another; :meth:`snapshot` is what a page shows."""

    def __init__(self, settings: dict, subs: list, *, label: str = "Подписки", kind: str = "check",
                 run_mode: str | None = None, forget_seeds: bool = False):
        global _SEQ
        with _LOCK:
            _SEQ += 1
            self.id = _SEQ
        self.settings = dict(settings)
        self.subs = [dict(s) for s in subs]
        self.label = label
        self.kind = kind                          # check | auto | tag
        self.run_mode = run_mode
        self.forget_seeds = forget_seeds          # «Скачать тег»: the found-post list goes when it is done
        self.owner = f"«{label}», запуск в {time.strftime('%H:%M:%S')} (#{self.id})"
        self.status = "starting"                  # starting → running ⇄ waiting → stopping → done / failed
        self.started_at = time.time()
        self.finished_at = 0.0
        self.index = 0
        self.current_name = ""
        self.downloaded = 0
        self.results: list = []
        self.error = ""
        self.plan: dict = {}
        self._plan_seq = 0                        # questions asked so far (a page asks each one once)
        self._decision = threading.Event()
        self._allowed = False
        self._ask_lock = threading.Lock()         # the question, its answer and the status change together
        self._stop_requested = False              # run_subscription reads it (stop_flag)
        self._paused = threading.Event()          # (v5.44.120) «Пауза» of «Загрузки»: the next file waits
        self._skip_now = False                    # (v5.44.120) «Пропустить подписку»: the one going stops, the next goes
        self.skipped: list = []                   # the names of the subscriptions skipped
        self.files: list = []                     # (v5.44.141) the files the run downloaded (favorites → the parser)
        self._log: deque = deque(maxlen=LOG_KEEP)
        self._seq = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._exit_waiter = _ExitWaiter(self)

    # ── control ─────────────────────────────────────────────────────────────
    def start(self) -> "SubscriptionJob":
        global _CURRENT
        from core.shutdown import is_shutting_down
        if not self.subs:
            raise ValueError("нечего проверять")
        if is_shutting_down():
            raise SubscriptionsBusy("закрытие программы", "Программа закрывается — подписки сейчас не проверяются")
        if not claim(self.owner):
            raise SubscriptionsBusy(owner())
        with _LOCK:
            _CURRENT = self
        self.status = "running"
        self._thread = threading.Thread(target=self._run, name=f"subscription-job-{self.id}", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self.status, self.finished_at = "failed", time.time()
            with _LOCK:
                if _CURRENT is self:
                    _CURRENT = None
            release(self.owner)
            raise
        _hook_exit()
        from core.shutdown import register_qthread
        register_qthread(self._exit_waiter, f"subscription job #{self.id}")    # the exit waits for it
        if is_shutting_down():          # the exit began meanwhile: its hook may have looked before this job was here
            self.stop(by_user=False)
        return self

    def stop(self, *, by_user: bool = True) -> None:
        global _AUTO_PAUSED_UNTIL
        with self._ask_lock:
            if self.status not in {"running", "waiting", "starting"}:
                return
            self._stop_requested = True
            self.status = "stopping"
            self._allowed = False
            self._decision.set()
            self._paused.clear()                  # (v5.44.120) a stop while paused: not paused any more
        if by_user:
            with _LOCK:
                _AUTO_PAUSED_UNTIL = time.time() + AUTO_PAUSE_SECONDS
            self._say(f"СТОП: доделываю текущий файл; найденное скачается при следующей проверке. "
                      f"Автопроверка отдыхает {AUTO_PAUSE_SECONDS // 60} мин")
        else:
            self._say("СТОП: программа закрывается, доделываю текущий файл")

    # (v5.44.120) «Пауза» / «Продолжить» and «Пропустить подписку» of «Загрузки»
    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> bool:
        """The check waits before its next file; False — it does not go."""
        with self._ask_lock:
            if self.status not in {"running", "waiting", "starting"}:
                return False
            self._paused.set()
        self._say("ПАУЗА: текущий файл докачается, следующий ждёт «Продолжить»")
        return True

    def resume(self) -> None:
        if self._paused.is_set():
            self._paused.clear()
            self._say("ПРОДОЛЖАЮ")

    def can_skip(self) -> bool:
        """«Пропустить подписку» means something: a subscription goes and another one comes after it."""
        return self.kind != "grab" and self.status in {"running", "waiting"} and 0 < self.index < len(self.subs) \
            and not self._stop_requested

    def skip(self) -> bool:
        """The subscription going stops (its found posts wait), the check goes on with the next; False — none to skip."""
        with self._ask_lock:
            if not self.can_skip():
                return False
            self._skip_now = True
            if self.status == "waiting":               # its large download asked: not this time
                self._allowed = False
                self._decision.set()
        self._say(f"ПРОПУСКАЮ «{self.current_name}»: найденное скачается при её следующей проверке")
        return True

    def decide(self, allowed: bool, seq: int | None) -> bool:
        """The answer to the large-download question, naming it: ``seq`` is the number of the question the person
        saw (``plan["seq"]``).  False when nothing is asked now, when ``seq`` is not the question asked now (the
        answer to a question that is gone must not answer the next one), or when this one is answered already."""
        with self._ask_lock:
            if self.status != "waiting" or seq is None or seq != self._plan_seq or self._decision.is_set():
                return False
            self._allowed = bool(allowed)
            self._decision.set()
            return True

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def _stop_for_exit(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self.stop(by_user=False)
            self._thread.join(20)

    # ── the run ─────────────────────────────────────────────────────────────
    def _fresh(self, sub: dict) -> dict | None:
        """The subscription as it is now: one edited, switched off or deleted while it waited its
        turn is checked as it is now (or skipped), not as it was when the check began."""
        if sub.get("one_off") or not sub.get("id"):
            return sub
        from core.subscriptions import get_subscription
        now = get_subscription(str(sub["id"]))
        name = str(sub.get("name") or sub.get("query") or sub["id"])
        if now is None:
            self._say(f"── «{name}»: подписка удалена — пропускаю")
            return None
        if not now.get("enabled", True) and (self.kind == "auto" or len(self.subs) > 1):
            self._say(f"── «{name}»: подписка выключена — пропускаю")
            return None
        return now

    def _run(self) -> None:
        global _CURRENT, _LAST
        from core.subscriptions import run_subscription
        try:
            for n, queued in enumerate(self.subs, 1):
                if self._stop_requested:
                    break
                self.index = n
                sub = self._fresh(queued)
                if sub is None:
                    continue
                self.current_name = str(sub.get("name") or sub.get("query") or sub.get("id") or "подписка")
                if len(self.subs) > 1:
                    self._say(f"── {n}/{len(self.subs)}: {self.current_name}")
                result = {"id": str(sub.get("id") or ""), "name": self.current_name, "downloaded": 0}
                self.results.append(result)
                self._skip_now = False
                try:
                    got = run_subscription(sub, self.settings, log=self._say, progress=self._on_progress,
                                           stop_flag=_EngineFlag(self), on_file_ready=self._file_ready,
                                           run_mode=self.run_mode or sub.get("run_mode") or "all",
                                           confirm_plan=self._confirm_plan)
                    result["downloaded"] = int(got or 0)
                except Exception as exc:      # one subscription failing does not stop the others
                    result["error"] = f"{type(exc).__name__}: {exc}"
                    self._say(f"ОШИБКА ПОДПИСКИ «{self.current_name}»: {result['error']}")
                    self._say(traceback.format_exc()[-2000:])
                    continue
                finally:
                    self.downloaded = sum(int(r.get("downloaded") or 0) for r in self.results)
                    if self._skip_now:            # (v5.44.120) «Пропустить подписку»: checked now, the next goes
                        self._skip_now = False
                        result["skipped"] = True
                        self.skipped.append(self.current_name)
                        if sub.get("id") and not sub.get("one_off"):
                            from core.subscriptions import record_run
                            record_run(str(sub["id"]), last_check=int(time.time()))
                if self.forget_seeds and not self._stop_requested and sub.get("id"):
                    from core.subscription_engine.seed_cache import clear_seeds_for_subscription
                    clear_seeds_for_subscription(str(sub["id"]))
            with self._ask_lock:                  # a «Остановить» pressed just now does not undo the end
                self.status = "done"
        except BaseException as exc:              # shown on the page, never a silent death
            self.error = f"{type(exc).__name__}: {exc}"
            with self._ask_lock:
                self.status = "failed"
            self._say(f"ПОДПИСКИ ОСТАНОВЛЕНЫ ОШИБКОЙ: {self.error}")
            self._say(traceback.format_exc()[-2000:])
        finally:
            self.finished_at = time.time()
            self.current_name = ""
            self.plan = {}
            self._paused.clear()
            with _LOCK:
                if _CURRENT is self:
                    _CURRENT = None
                _LAST = self
            release(self.owner)
            self._say(f"ГОТОВО: скачано файлов — {self.downloaded}" + (" (остановлено)" if self._stop_requested else ""))
            for hook in list(ENDED):              # (v5.44.120) «Загрузки → Готово»
                try:
                    hook(self)
                except Exception as exc:  # noqa: BLE001 - a record of the history not made: the check is over anyway
                    self._say(f"(запись в «Готово» не сделана: {type(exc).__name__})")

    def _file_ready(self, path: str) -> None:
        if len(self.files) < FILES_KEPT:
            self.files.append(str(path))

    def _on_progress(self, downloaded_in_sub: int) -> None:
        # run_subscription counts the files of the subscription it is checking
        before = sum(int(r.get("downloaded") or 0) for r in self.results[:-1])
        self.downloaded = before + int(downloaded_in_sub or 0)

    def _confirm_plan(self, plan: dict) -> bool:
        """A large download asks first (as the old page did); the check waits for the answer."""
        disk = plan.get("disk") or {}
        with self._ask_lock:
            self._decision.clear()
            if self._stop_requested:
                return False
            self._plan_seq += 1
            self.plan = {"seq": self._plan_seq, "files": int(plan.get("groups") or 0), "known_files": int(plan.get("known_files") or 0),
                         "known_bytes": int(plan.get("known_bytes") or 0), "disk_free": int(disk.get("free") or 0),
                         "not_enough_space": bool(plan.get("not_enough_space")), "name": self.current_name}
            self.status = "waiting"
        self._say(f"БОЛЬШАЯ ЗАГРУЗКА: {self.plan['files']} файлов — жду ответа «Скачать» или «Не скачивать»")
        self._decision.wait(PLAN_WAIT_SECONDS)
        with self._ask_lock:                  # an answer is either in by now or never counts
            answered = self._decision.is_set()
            allowed = bool(answered and self._allowed and not self._stop_requested)
            if self.status == "waiting":
                self.status = "running"
            self.plan = {}
        if not answered:
            self._say(f"ответа нет {PLAN_WAIT_SECONDS // 60} мин — в этот раз не скачиваю")
        else:
            self._say("скачиваю" if allowed else "не скачиваю")
        return allowed

    def _say(self, message: str) -> None:
        from core.redaction import sanitize_text
        text = sanitize_text(message)          # API keys in an error's URL never reach a page
        with self._lock:
            for line in text.splitlines() or [""]:
                self._seq += 1
                self._log.append((self._seq, time.time(), line))

    # ── what a page shows ───────────────────────────────────────────────────
    def snapshot(self, after: int = 0, limit: int = 800) -> dict:
        with self._lock:
            lines = [x for x in self._log if x[0] > after][-max(1, limit):]
            last = self._seq
        with self._ask_lock:                  # «waiting» always comes with its question
            status, plan = self.status, dict(self.plan)
        now = self.finished_at or time.time()
        return {"id": self.id, "label": self.label, "kind": self.kind, "status": status,
                "index": self.index, "count": len(self.subs), "current": self.current_name,
                "downloaded": self.downloaded, "elapsed": int(max(0.0, now - self.started_at)),
                "plan": plan, "results": [dict(r) for r in self.results], "error": self.error,
                "paused": self.paused and status in {"running", "waiting"}, "skipped": list(self.skipped),
                "log": [{"seq": s, "t": int(t), "text": x} for s, t, x in lines], "last_seq": last}


class _EngineFlag:
    """(v5.44.120) The stop flag the engine (``subscriptions.run_subscription``) reads between files and between sites:
    while «Пауза» — it waits there (the file on its way has come); «Остановить» or «Пропустить подписку» — a stop of
    the subscription going (the check itself goes on after a skip)."""

    def __init__(self, job: SubscriptionJob):
        self._job = job

    @property
    def _stop_requested(self) -> bool:
        job = self._job
        while job._paused.is_set() and not job._stop_requested and not job._skip_now:
            time.sleep(0.2)
        return bool(job._stop_requested or job._skip_now)


class _ExitWaiter:
    """A check of subscriptions as ``core.shutdown.join_qthreads`` waits for threads at the exit (the job's own
    ``wait`` counts seconds, Qt's milliseconds): a check started after the exit hook looked is waited for too."""

    def __init__(self, job: SubscriptionJob):
        self._job = job

    def isRunning(self) -> bool:  # noqa: N802 - the QThread protocol
        thread = self._job._thread
        return thread is not None and thread.is_alive()

    def requestInterruption(self) -> None:  # noqa: N802
        self._job.stop(by_user=False)

    def wait(self, msecs: int | None = None) -> bool:
        return self._job.wait(None if msecs is None else max(0.0, msecs / 1000.0))


def _hook_exit() -> None:
    """Closing the program stops the check in progress (one hook for all checks: a hook per check
    would keep every finished one, with its journal, until the program closes)."""
    global _EXIT_HOOKED
    with _LOCK:
        if _EXIT_HOOKED:
            return
        _EXIT_HOOKED = True
    from core.shutdown import register
    register("subscription jobs", _stop_current_for_exit)


def _stop_current_for_exit() -> None:
    with _LOCK:
        job = _CURRENT
    if job is not None:
        job._stop_for_exit()


def start(settings: dict, subs: list, **kwargs) -> SubscriptionJob:
    """Start a check (raises :class:`SubscriptionsBusy` while another one is going).  A check of
    subscriptions started by hand («Проверить») wakes the automatic check if «Остановить» put it
    to rest; «Скачать тег» and the grabber's downloads do not (they say nothing about subscriptions)."""
    job = SubscriptionJob(settings, subs, **kwargs).start()
    if job.kind == "check":
        resume_auto()
    return job


def start_due(settings: dict) -> "SubscriptionJob | None":
    """The automatic check: the subscriptions whose time has come, if nothing is being checked and
    «Остановить» has not put it to rest.  Called once a minute by the program: ``core/background_duties`` in the
    new window, the page «Подписки» of the old one."""
    from core.shutdown import is_shutting_down
    if is_running() or auto_paused_until() or is_shutting_down():
        return None
    from core.subscriptions import due_subscriptions
    due = due_subscriptions()
    if not due:
        return None
    try:
        return start(settings, due, label="Автопроверка", kind="auto")
    except SubscriptionsBusy:
        return None
