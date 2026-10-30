"""Process-wide registry for cooperative background work.

This registry is deliberately separate from :mod:`core.heartbeat`.
Heartbeats describe a currently executing low-level operation (usually one
network call).  Background tasks describe user-visible work and its lifecycle:
queued, running, waiting, cancelling, completed, failed, or cancelled.

The registry is memory-only.  It must never become a second source of truth for
library state; durable queues and operation journals remain in SQLite.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import itertools
import threading
import time
from typing import Any, Callable, Iterator

ACTIVE_STATES = {"queued", "running", "waiting", "paused", "cancelling"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
VALID_STATES = ACTIVE_STATES | TERMINAL_STATES


@dataclass(frozen=True)
class TaskRef:
    task_id: str
    name: str
    kind: str


class BackgroundTaskRegistry:
    def __init__(self, *, history_limit: int = 500) -> None:
        self._lock = threading.RLock()
        self._tasks: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._counter = itertools.count(1)
        self._history_limit = max(50, int(history_limit or 500))
        self._local = threading.local()

    # ------------------------------------------------------------------ ids/context
    def _next_id(self) -> str:
        return f"bg-{next(self._counter):08d}"

    def current_task_id(self) -> str:
        stack = getattr(self._local, "stack", None) or []
        return str(stack[-1]) if stack else ""

    @contextmanager
    def bind(self, task_id: str) -> Iterator[str]:
        key = str(task_id or "")
        stack = list(getattr(self._local, "stack", None) or [])
        stack.append(key)
        self._local.stack = stack
        try:
            yield key
        finally:
            current = list(getattr(self._local, "stack", None) or [])
            if current:
                current.pop()
            self._local.stack = current

    # ------------------------------------------------------------------ lifecycle
    def register(
        self,
        name: str,
        *,
        kind: str = "background",
        parent_id: str | None = None,
        task_id: str | None = None,
        state: str = "queued",
        detail: str = "",
        progress_text: str = "ожидает запуска",
        current: int | float | None = None,
        total: int | float | None = None,
        cancellable: bool = False,
        cancel_callback: Callable[[], Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        now = time.time()
        key = str(task_id or self._next_id())
        state = str(state or "queued").lower()
        if state not in VALID_STATES:
            state = "queued"
        parent = str(parent_id or self.current_task_id() or "")
        with self._lock:
            existing = self._tasks.get(key)
            if existing and existing.get("state") in ACTIVE_STATES:
                raise ValueError(f"background task already active: {key}")
            self._tasks[key] = {
                "task_id": key,
                "name": str(name or "background task"),
                "kind": str(kind or "background"),
                "parent_id": parent,
                "state": state,
                "detail": str(detail or ""),
                "progress_text": str(progress_text or ""),
                "current": current,
                "total": total,
                "created_at": now,
                "started_at": now if state == "running" else None,
                "finished_at": now if state in TERMINAL_STATES else None,
                "last_activity_at": now,
                "last_descendant_activity_at": 0.0,
                "last_descendant_id": "",
                "last_descendant_detail": "",
                "thread_id": int(threading.get_ident()),
                "thread_name": threading.current_thread().name,
                "cancellable": bool(cancellable or cancel_callback),
                "cancel_requested": False,
                "cancel_callback": cancel_callback,
                "error": "",
                "metadata": dict(metadata or {}),
            }
            self._tasks.move_to_end(key)
            self._touch_ancestors_locked(key, now, str(detail or progress_text or ""))
            self._trim_locked()
        return key

    def start(self, task_id: str, *, detail: str = "", progress_text: str = "выполняется") -> bool:
        return self.update(task_id, state="running", detail=detail, progress_text=progress_text, mark_started=True)

    def update(
        self,
        task_id: str | None = None,
        *,
        state: str | None = None,
        detail: str | None = None,
        progress_text: str | None = None,
        current: int | float | None = None,
        total: int | float | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        mark_started: bool = False,
        touch: bool = True,
    ) -> bool:
        key = str(task_id or self.current_task_id() or "")
        if not key:
            return False
        now = time.time()
        with self._lock:
            row = self._tasks.get(key)
            if not row:
                return False
            if state is not None:
                value = str(state or "").lower()
                if value not in VALID_STATES:
                    raise ValueError(f"invalid background task state: {value}")
                row["state"] = value
                if value == "running" and not row.get("started_at"):
                    row["started_at"] = now
                if value in TERMINAL_STATES:
                    row["finished_at"] = now
            if mark_started and not row.get("started_at"):
                row["started_at"] = now
            if detail is not None:
                row["detail"] = str(detail or "")
            if progress_text is not None:
                row["progress_text"] = str(progress_text or "")
            if current is not None:
                row["current"] = current
            if total is not None:
                row["total"] = total
            if error is not None:
                row["error"] = str(error or "")[:4000]
            if metadata:
                row.setdefault("metadata", {}).update(dict(metadata))
            if touch:
                row["last_activity_at"] = now
                row["thread_id"] = int(threading.get_ident())
                row["thread_name"] = threading.current_thread().name
                activity_detail = str(detail if detail is not None else progress_text if progress_text is not None else row.get("detail") or "")
                self._touch_ancestors_locked(key, now, activity_detail)
            self._tasks.move_to_end(key)
            self._trim_locked()
        return True

    def touch(self, task_id: str | None = None, *, detail: str | None = None) -> bool:
        return self.update(task_id, detail=detail, touch=True)

    def progress(
        self,
        task_id: str | None = None,
        *,
        text: str | None = None,
        current: int | float | None = None,
        total: int | float | None = None,
        detail: str | None = None,
    ) -> bool:
        return self.update(task_id, progress_text=text, current=current, total=total, detail=detail, touch=True)

    def set_state(self, task_id: str | None, state: str, *, detail: str | None = None, progress_text: str | None = None) -> bool:
        return self.update(task_id, state=state, detail=detail, progress_text=progress_text, touch=True)

    def finish(self, task_id: str | None = None, *, detail: str = "", progress_text: str = "завершено") -> bool:
        return self.update(task_id, state="completed", detail=detail, progress_text=progress_text, touch=True)

    def fail(self, task_id: str | None = None, error: BaseException | str = "", *, detail: str = "") -> bool:
        text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error or "")
        return self.update(task_id, state="failed", detail=detail or text, progress_text="ошибка", error=text, touch=True)

    def cancel(self, task_id: str | None = None, *, detail: str = "") -> bool:
        return self.update(task_id, state="cancelled", detail=detail, progress_text="остановлено", touch=True)

    def request_cancel(self, task_id: str) -> bool:
        key = str(task_id or "")
        callback = None
        with self._lock:
            row = self._tasks.get(key)
            if not row or row.get("state") not in ACTIVE_STATES or not row.get("cancellable"):
                return False
            row["cancel_requested"] = True
            row["state"] = "cancelling"
            row["progress_text"] = "запрошена остановка"
            row["last_activity_at"] = time.time()
            callback = row.get("cancel_callback")
        if callable(callback):
            try:
                callback()
            except Exception:
                return False
        return True

    @contextmanager
    def task(
        self,
        name: str,
        *,
        kind: str = "background",
        parent_id: str | None = None,
        task_id: str | None = None,
        detail: str = "",
        cancellable: bool = False,
        cancel_callback: Callable[[], Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[TaskRef]:
        key = self.register(
            name,
            kind=kind,
            parent_id=parent_id,
            task_id=task_id,
            detail=detail,
            cancellable=cancellable,
            cancel_callback=cancel_callback,
            metadata=metadata,
        )
        self.start(key, detail=detail)
        ref = TaskRef(key, str(name), str(kind))
        try:
            with self.bind(key):
                yield ref
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit, InterruptedError)):
                self.cancel(key, detail=str(exc))
            else:
                self.fail(key, exc)
            raise
        else:
            row = self.get(key)
            if row and row.get("state") in ACTIVE_STATES:
                if row.get("cancel_requested"):
                    self.cancel(key)
                else:
                    self.finish(key)

    # ------------------------------------------------------------------ queries
    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._tasks.get(str(task_id or ""))
            return self._public_row_locked(row) if row else None

    def active_snapshot(self, *, include_parents: bool = True) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = [self._public_row_locked(row, now=now) for row in self._tasks.values() if row.get("state") in ACTIVE_STATES]
        if not include_parents:
            parents = {str(row.get("parent_id") or "") for row in rows if row.get("parent_id")}
            rows = [row for row in rows if row.get("task_id") not in parents]
        rows.sort(key=lambda row: (float(row.get("created_at") or 0), str(row.get("task_id") or "")))
        return rows

    def recent_snapshot(self, limit: int = 100) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = [self._public_row_locked(row, now=now) for row in self._tasks.values()]
        rows.sort(key=lambda row: float(row.get("finished_at") or row.get("created_at") or 0), reverse=True)
        return rows[: max(0, int(limit or 0))]

    def stale_snapshot(
        self,
        threshold_seconds: float,
        *,
        leaf_only: bool = True,
        waiting_threshold_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return genuinely stale active tasks.

        Ordinary ``waiting`` is an explained cooperative state and therefore
        does not use the normal running threshold.  Callers may provide a much
        larger waiting threshold so an abandoned/dead waiting task is still
        diagnosed eventually instead of being hidden forever.
        """
        threshold = max(0.1, float(threshold_seconds or 0.1))
        waiting_threshold = None
        if waiting_threshold_seconds is not None:
            waiting_threshold = max(threshold, float(waiting_threshold_seconds or threshold))
        rows = self.active_snapshot(include_parents=True)
        parents = {str(row.get("parent_id") or "") for row in rows if row.get("parent_id")}
        stale = []
        for row in rows:
            state = str(row.get("state") or "")
            if state == "running":
                row_threshold = threshold
            elif state == "waiting" and waiting_threshold is not None:
                row_threshold = waiting_threshold
            else:
                continue
            if leaf_only and row.get("task_id") in parents:
                continue
            if float(row.get("effective_idle_seconds") or 0.0) >= row_threshold:
                stale.append(row)
        stale.sort(key=lambda row: float(row.get("effective_idle_seconds") or 0.0), reverse=True)
        return stale

    def clear_finished(self) -> int:
        with self._lock:
            keys = [key for key, row in self._tasks.items() if row.get("state") in TERMINAL_STATES]
            for key in keys:
                self._tasks.pop(key, None)
            return len(keys)

    # ------------------------------------------------------------------ internals
    def _touch_ancestors_locked(self, child_id: str, now: float, detail: str) -> None:
        seen = {str(child_id)}
        child = self._tasks.get(str(child_id))
        parent_id = str((child or {}).get("parent_id") or "")
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = self._tasks.get(parent_id)
            if not parent:
                break
            parent["last_descendant_activity_at"] = now
            parent["last_descendant_id"] = str(child_id)
            parent["last_descendant_detail"] = str(detail or "")
            parent_id = str(parent.get("parent_id") or "")

    def _public_row_locked(self, row: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else float(now)
        out = {k: v for k, v in row.items() if k != "cancel_callback"}
        own = float(row.get("last_activity_at") or row.get("created_at") or now)
        child = float(row.get("last_descendant_activity_at") or 0.0)
        effective = max(own, child)
        started = float(row.get("started_at") or row.get("created_at") or now)
        finished = float(row.get("finished_at") or 0.0)
        end = finished if finished else now
        out["elapsed_seconds"] = round(max(0.0, end - started), 1)
        out["idle_seconds"] = round(max(0.0, now - own), 1)
        out["effective_idle_seconds"] = round(max(0.0, now - effective), 1)
        if row.get("total") not in (None, 0):
            try:
                out["progress_percent"] = round(max(0.0, min(100.0, float(row.get("current") or 0) * 100.0 / float(row.get("total")))), 1)
            except Exception:
                out["progress_percent"] = None
        else:
            out["progress_percent"] = None
        return out

    def _trim_locked(self) -> None:
        terminal = [key for key, row in self._tasks.items() if row.get("state") in TERMINAL_STATES]
        excess = max(0, len(terminal) - self._history_limit)
        for key in terminal[:excess]:
            self._tasks.pop(key, None)


_REGISTRY = BackgroundTaskRegistry()


def get_background_task_registry() -> BackgroundTaskRegistry:
    return _REGISTRY


@contextmanager
def background_task(*args, **kwargs):
    with _REGISTRY.task(*args, **kwargs) as ref:
        yield ref


def active_tasks(*, include_parents: bool = True) -> list[dict[str, Any]]:
    return _REGISTRY.active_snapshot(include_parents=include_parents)


def recent_tasks(limit: int = 100) -> list[dict[str, Any]]:
    return _REGISTRY.recent_snapshot(limit)


def _reset_for_tests() -> None:
    global _REGISTRY
    _REGISTRY = BackgroundTaskRegistry()


__all__ = [
    "ACTIVE_STATES",
    "TERMINAL_STATES",
    "VALID_STATES",
    "BackgroundTaskRegistry",
    "TaskRef",
    "get_background_task_registry",
    "background_task",
    "active_tasks",
    "recent_tasks",
]
