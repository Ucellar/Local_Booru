from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any
import traceback
import logging
import time

from core.performance import record_slow_operation

from PySide6.QtCore import QObject, Signal


class TaskSignals(QObject):
    progress = Signal(str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


@dataclass
class TaskHandle:
    name: str
    future: Future | None
    signals: TaskSignals
    _cancelled: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    progress_text: str = "ожидает запуска"
    state: str = "queued"

    def cancel(self):
        self._cancelled = True
        self.state = "cancelling"
        self.progress_text = "запрошена остановка"
        try:
            if self.future is not None:
                self.future.cancel()
        except Exception:
            pass

    def cancelled(self):
        return self._cancelled or bool(self.future and self.future.cancelled())

    def elapsed_seconds(self) -> float:
        start = self.started_at or self.created_at
        return max(0.0, time.time() - start)


class TaskManager(QObject):
    task_started = Signal(str)
    task_failed = Signal(str, str)
    task_completed = Signal(str, float)
    task_progress = Signal(str, str)
    task_cancelled = Signal(str)

    """Central cooperative background task manager.

    Any long operation submitted here receives optional ``progress`` and
    ``stop_check`` callables. Cancellation is cooperative for operations already
    running; queued work is cancelled immediately.
    """

    def __init__(self, parent=None, max_workers=2):
        super().__init__(parent)
        try:
            from core.shutdown import register
            register("task manager", self.shutdown)
        except Exception:
            pass
        self.pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers or 2)), thread_name_prefix="local-booru")
        self.tasks: list[TaskHandle] = []
        self._logger = logging.getLogger("local_booru.tasks")

    def submit(self, func: Callable[..., Any], *args, name="task", on_progress=None, on_result=None, on_error=None, on_finished=None, **kwargs):
        signals = TaskSignals()
        if on_progress: signals.progress.connect(on_progress)
        if on_result: signals.result.connect(on_result)
        if on_error: signals.error.connect(on_error)
        if on_finished: signals.finished.connect(on_finished)

        handle = TaskHandle(name=str(name), future=None, signals=signals)
        self.tasks.append(handle)

        def progress(msg):
            handle.progress_text = str(msg)
            self.task_progress.emit(handle.name, handle.progress_text)
            signals.progress.emit(handle.progress_text)

        def stop_check():
            return handle.cancelled()

        def runner():
            started = time.perf_counter()
            handle.started_at = time.time()
            handle.state = "running"
            handle.progress_text = "выполняется"
            self.task_started.emit(handle.name)
            try:
                if handle.cancelled():
                    handle.state = "cancelled"
                    self.task_cancelled.emit(handle.name)
                    return None
                import inspect
                sig = inspect.signature(func)
                call_kwargs = dict(kwargs)
                if "progress" in sig.parameters and "progress" not in call_kwargs:
                    call_kwargs["progress"] = progress
                if "stop_check" in sig.parameters and "stop_check" not in call_kwargs:
                    call_kwargs["stop_check"] = stop_check
                res = func(*args, **call_kwargs)
                if handle.cancelled():
                    handle.state = "cancelled"
                    handle.progress_text = "остановлено"
                    self.task_cancelled.emit(handle.name)
                else:
                    handle.state = "completed"
                    handle.progress_text = "завершено"
                    signals.result.emit(res)
                return res
            except Exception:
                handle.state = "failed"
                handle.progress_text = "ошибка"
                text = traceback.format_exc()
                self._logger.error("Background task failed: %s\n%s", name, text)
                self.task_failed.emit(handle.name, text)
                signals.error.emit(text)
                raise
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                record_slow_operation("task." + handle.name, elapsed_ms, detail={"cancelled": bool(stop_check()), "state": handle.state})
                self.task_completed.emit(handle.name, float(elapsed_ms))
                signals.finished.emit()

        future = self.pool.submit(runner)
        handle.future = future
        signals.finished.connect(lambda _h=handle: self._forget_finished(_h))
        if future.done():
            self._forget_finished(handle)
        return handle

    def _forget_finished(self, handle):
        try:
            self.tasks = [task for task in self.tasks if task is not handle and not bool(task.future and task.future.done())]
        except Exception:
            pass

    def active_snapshot(self):
        """Return user-visible state of current tasks without mutating them."""
        out = []
        for task in list(self.tasks):
            try:
                if task.future is not None and task.future.done():
                    continue
                out.append({
                    "name": task.name,
                    "cancelled": task.cancelled(),
                    "state": task.state,
                    "progress": task.progress_text,
                    "elapsed_seconds": round(task.elapsed_seconds(), 1),
                })
            except Exception:
                continue
        return out

    def cancel_all(self) -> int:
        count = 0
        for task in list(self.tasks):
            try:
                if task.future is None or not task.future.done():
                    task.cancel()
                    self.task_cancelled.emit(task.name)
                    count += 1
            except Exception:
                pass
        return count

    def shutdown(self):
        self.cancel_all()
        try:
            self.pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            try: self.pool.shutdown(wait=False)
            except Exception: pass
