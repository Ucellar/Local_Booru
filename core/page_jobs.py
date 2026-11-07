"""Long tasks a page starts that are not the parser or the subscriptions — a duplicate scan, a
library check: one task of a kind at a time for the whole program, run in a thread, its journal
(numbered lines), its result and «Остановить» for the page that polls it.

``fn(progress, stop_check)`` does the work: ``progress(text)`` adds journal lines, ``stop_check()``
says whether «Остановить» was pressed; what it returns is the task's result.  A dict result with
"stopped" decides the end state (work that cannot stop half-way ends «done» even after «Остановить»);
without it, a pressed «Остановить» means «stopped».  A dict result with "failed" (the reason, in words)
ends it «failed» without a traceback: the work refused for a reason the person can act on.  Work that
has no place to stop at all is started with ``stoppable=False``: «Остановить» then says so in the
journal instead of promising a stop.

At the exit every task is asked to stop and waited for like the program's threads (``core.shutdown.join_qthreads``,
``_ExitJoin``): one still going after the exit's budget makes the exit an unclean one — no checkpoint and no copy of
a base it is still writing.
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from typing import Callable

LOG_KEEP = 2000
_LOCK = threading.Lock()
_TASKS: dict = {}                 # kind -> the task in progress, else the last one
_SEQ = 0
_EXIT_HOOKED = False


class TaskBusy(RuntimeError):
    def __init__(self, label: str):
        super().__init__(f"Уже идёт: {label}")
        self.label = label


class PageTask:
    def __init__(self, kind: str, label: str, fn: Callable, *, stoppable: bool = True, about=None):
        global _SEQ
        with _LOCK:
            _SEQ += 1
            self.id = _SEQ
        self.kind = kind
        self.label = label
        self.fn = fn
        self.stoppable = bool(stoppable)
        self.about = about                # what the task works on (a card number…), for the page that shows it
        self.status = "starting"          # starting → running → stopping → done / failed / stopped
        self.started_at = time.time()
        self.finished_at = 0.0
        self.result = None
        self.error = ""
        self._stop = threading.Event()
        self._log: deque = deque(maxlen=LOG_KEEP)
        self._seq = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.status in {"starting", "running", "stopping"}

    def start(self) -> "PageTask":
        self.status = "running"
        self._thread = threading.Thread(target=self._run, name=f"page-task-{self.kind}-{self.id}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> bool:
        """Ask the work to stop; False when it cannot be stopped half-way (it is said in the journal)."""
        if self.status in {"starting", "running"}:
            if not self.stoppable:
                self._say("Это не прерывается на середине — дождитесь конца")
                return False
            self.status = "stopping"
            self._stop.set()
            self._say("СТОП: останавливаю после текущего шага")
        return True

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def _run(self) -> None:
        try:
            self.result = self.fn(self._say, self._stop.is_set)
            stopped = self._stop.is_set()
            if isinstance(self.result, dict) and "stopped" in self.result:
                stopped = bool(self.result["stopped"])    # the work says whether it really stopped half-way
            failed = str(self.result.get("failed") or "") if isinstance(self.result, dict) else ""
            if failed:                        # a refusal in words: the journal says it, no traceback
                from core.redaction import sanitize_text
                self.error = sanitize_text(failed)
                self._say(f"НЕ ПОЛУЧИЛОСЬ: {self.error}")
                self.status = "failed"
            else:
                self.status = "stopped" if stopped else "done"
        except BaseException as exc:          # shown on the page, never a silent death
            from core.redaction import sanitize_text
            self.error = sanitize_text(f"{type(exc).__name__}: {exc}")
            self.status = "stopped" if self._stop.is_set() else "failed"
            self._say(f"ОШИБКА: {self.error}")
            self._say(traceback.format_exc()[-2000:])
        finally:
            self.finished_at = time.time()
            if not (self.status == "failed" and isinstance(self.result, dict) and self.result.get("failed")):
                self._say("ГОТОВО" if self.status == "done" else ("ОСТАНОВЛЕНО" if self.status == "stopped" else "ПРЕРВАНО ОШИБКОЙ"))

    def _say(self, message: str) -> None:
        from core.redaction import sanitize_text
        text = sanitize_text(message)
        with self._lock:
            for line in text.splitlines() or [""]:
                self._seq += 1
                self._log.append((self._seq, time.time(), line))

    def snapshot(self, after: int = 0, limit: int = 500) -> dict:
        with self._lock:
            lines = [x for x in self._log if x[0] > after][-max(1, limit):]
            last = self._seq
        now = self.finished_at or time.time()
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
                "stoppable": self.stoppable,
                "elapsed": int(max(0.0, now - self.started_at)), "error": self.error,
                "log": [{"seq": s, "t": int(t), "text": x} for s, t, x in lines], "last_seq": last}


class _ExitJoin:
    """A page task as ``core.shutdown.join_qthreads`` sees a thread: the exit waits for it within its budget, and an
    exit that could not is not a clean one (no checkpoint, no copy over a base the task is still writing)."""

    def __init__(self, task: PageTask):
        self._task = task

    def isRunning(self) -> bool:  # noqa: N802 - the QThread protocol
        thread = self._task._thread
        return thread is not None and thread.is_alive()

    def requestInterruption(self) -> None:  # noqa: N802
        if self._task.stoppable:
            self._task.stop()

    def wait(self, msecs: int | None = None) -> bool:
        return self._task.wait(None if msecs is None else max(0.0, msecs / 1000.0))


def start(kind: str, label: str, fn: Callable, *, stoppable: bool = True, about=None,
          replace: bool = False) -> PageTask:
    """Start a task of ``kind`` (raises :class:`TaskBusy` while one of that kind is going); (v5.44.93) ``replace`` —
    the one going is asked to stop and this one starts at once (a search: the last one asked is the one wanted; the
    old one ends on its own, its result is nobody's)."""
    global _EXIT_HOOKED
    task = PageTask(kind, label, fn, stoppable=stoppable, about=about)
    with _LOCK:
        old = _TASKS.get(kind)
        if old is not None and old.running:
            if not (replace and old.stoppable):
                raise TaskBusy(old.label)
            old.stop()
        _TASKS[kind] = task
        hook = not _EXIT_HOOKED
        _EXIT_HOOKED = True
    from core.shutdown import register, register_qthread
    if hook:
        register("page tasks", _stop_all_for_exit)
    task._exit_join = _ExitJoin(task)          # the registry keeps a weak reference: the task keeps it alive
    register_qthread(task._exit_join, f"page task {kind}")
    return task.start()


def current(kind: str) -> PageTask | None:
    with _LOCK:
        return _TASKS.get(kind)


def running() -> list:
    """The page tasks going now (of every kind), oldest first."""
    with _LOCK:
        return sorted((t for t in _TASKS.values() if t.running), key=lambda t: t.id)


def _stop_all_for_exit() -> None:
    """The exit asks every task to stop; it waits for them with the program's threads (``_ExitJoin``)."""
    with _LOCK:
        tasks = [t for t in _TASKS.values() if t.running]
    for task in tasks:
        task.stop()
