"""Robust task queue with retry (inspired by imgbrd-grabber pattern).

If a worker fails on a task:
  - task goes back to queue with attempt count+1
  - waits backoff_base * 1.25^attempt seconds
  - after max_attempts, task goes to dead_letter
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from typing import Any, Callable

log = logging.getLogger("local_booru.queue")


@dataclass(order=True)
class Task:
    priority: float          # lower = higher priority; use time.time() for FIFO
    attempt:  int = field(default=0, compare=False)
    payload:  Any = field(default=None, compare=False)
    label:    str = field(default="", compare=False)


class RetryQueue:
    """Priority queue with exponential-backoff retry.

    Usage:
        q = RetryQueue(max_attempts=3, backoff_base=2.0)
        q.put(payload, label="file.jpg")
        task = q.get(timeout=5)
        try:
            process(task.payload)
            q.task_done()
        except Exception as e:
            q.retry(task, error=e)  # re-queues with delay
    """

    def __init__(self,
                 max_attempts: int = 3,
                 backoff_base: float = 2.0,
                 backoff_factor: float = 1.25):
        self._q: PriorityQueue[Task] = PriorityQueue()
        self._dead: list[Task] = []
        self._lock = threading.Lock()
        self._unfinished = 0
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_factor = backoff_factor

    def put(self, payload: Any, label: str = "", priority: float | None = None) -> None:
        task = Task(
            priority=priority if priority is not None else time.time(),
            payload=payload,
            label=label,
        )
        with self._lock:
            self._unfinished += 1
        self._q.put(task)

    def get(self, timeout: float = 5.0) -> Task:
        return self._q.get(timeout=timeout)

    def task_done(self) -> None:
        with self._lock:
            self._unfinished = max(0, self._unfinished - 1)
        self._q.task_done()

    def retry(self, task: Task, error: Exception | None = None) -> bool:
        """Re-queue task with backoff. Returns False if dead-lettered."""
        task.attempt += 1
        if task.attempt >= self.max_attempts:
            log.error("Task dead-lettered after %d attempts [%s]: %s",
                      task.attempt, task.label, error)
            with self._lock:
                self._dead.append(task)
                self._unfinished = max(0, self._unfinished - 1)
            return False

        delay = self.backoff_base * (self.backoff_factor ** (task.attempt - 1))
        log.warning("Retry %d/%d [%s] in %.1fs: %s",
                    task.attempt, self.max_attempts, task.label, delay, error)
        # Schedule re-queue after delay
        task.priority = time.time() + delay

        def _requeue():
            time.sleep(delay)
            self._q.put(task)

        threading.Thread(target=_requeue, daemon=True).start()
        return True

    @property
    def pending(self) -> int:
        with self._lock:
            return self._unfinished

    @property
    def dead_letters(self) -> list[Task]:
        with self._lock:
            return list(self._dead)

    def clear_dead(self) -> None:
        with self._lock:
            self._dead.clear()
