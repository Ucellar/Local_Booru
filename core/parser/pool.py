"""The workers that read and write a parser run's files (core/parser/runner.py), taking their tasks by priority.

A file is read (its MD5, what the library knows of it) before any site is asked, and the same workers write what the
sites found.  With one queue in order of arrival, a read waited behind every write given to the workers before it:
on a big library with a slow disk hundreds of writes stood in front, the sites ran out of files and the run could
not tell how long it would take (the reading stage made no progress for many minutes).  Here a read goes before any
write waiting; writes keep their order among themselves.
"""
from __future__ import annotations

import heapq
import itertools
import threading
from concurrent.futures import Future

READ, WRITE = 0, 1          # priorities: smaller first


class PriorityPool:
    """``workers`` threads; ``submit(fn, *args, priority=…)`` → a ``concurrent.futures.Future`` (its callbacks, its
    exception — as with ``ThreadPoolExecutor``); the tasks of one priority in the order they came; ``shutdown`` (and
    leaving a ``with``) waits for every task given before it."""

    def __init__(self, workers: int, name: str = "parser-file"):
        self._cond = threading.Condition()
        self._heap: list = []
        self._order = itertools.count()
        self._closed = False
        self._threads = [threading.Thread(target=self._work, name=f"{name}_{n}", daemon=True)
                         for n in range(max(1, int(workers)))]
        for thread in self._threads:
            thread.start()

    def submit(self, fn, *args, priority: int = WRITE, **kwargs) -> Future:
        future: Future = Future()
        with self._cond:
            if self._closed:
                raise RuntimeError("cannot schedule new futures after shutdown")
            heapq.heappush(self._heap, (int(priority), next(self._order), future, fn, args, kwargs))
            self._cond.notify()
        return future

    def waiting(self, priority: int | None = None) -> int:
        """Tasks not taken by a worker yet (of one priority, or all)."""
        with self._cond:
            return sum(1 for item in self._heap if priority is None or item[0] == priority)

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._heap and not self._closed:
                    self._cond.wait()
                if not self._heap:
                    return                          # closed, and every task given is done
                _priority, _n, future, fn, args, kwargs = heapq.heappop(self._heap)
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # the task's own failure: its future carries it, as in ThreadPoolExecutor
                future.set_exception(exc)
            else:
                future.set_result(result)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._cond:
            self._closed = True
            if cancel_futures:
                for item in self._heap:
                    item[2].cancel()
                self._heap.clear()
            self._cond.notify_all()
        if wait:
            for thread in self._threads:
                thread.join()

    def __enter__(self) -> "PriorityPool":
        return self

    def __exit__(self, *_exc) -> bool:
        self.shutdown(wait=True)
        return False
