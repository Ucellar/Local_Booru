from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any
import traceback
import logging
import time

from core.performance import record_slow_operation
from core.background_tasks import get_background_task_registry

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
    registry_task_id: str = ""

    def cancel(self):
        self._cancelled = True
        self.state = "cancelling"
        self.progress_text = "запрошена остановка"
        try:
            if self.registry_task_id:
                get_background_task_registry().set_state(
                    self.registry_task_id, "cancelling", progress_text=self.progress_text
                )
        except Exception:
            pass
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
        registry = get_background_task_registry()
        task_kind = str(kwargs.pop("_task_kind", "task_manager") or "task_manager")
        parent_task_id = str(kwargs.pop("_parent_task_id", "") or "")
        registry_metadata = kwargs.pop("_task_metadata", None)
        handle.registry_task_id = registry.register(
            handle.name,
            kind=task_kind,
            parent_id=parent_task_id or None,
            state="queued",
            progress_text=handle.progress_text,
            cancellable=True,
            cancel_callback=handle.cancel,
            metadata=registry_metadata if isinstance(registry_metadata, dict) else None,
        )
        self.tasks.append(handle)

        def progress(msg):
            handle.progress_text = str(msg)
            registry.progress(handle.registry_task_id, text=handle.progress_text, detail=handle.progress_text)
            self.task_progress.emit(handle.name, handle.progress_text)
            signals.progress.emit(handle.progress_text)

        def stop_check():
            return handle.cancelled()

        def runner():
            started = time.perf_counter()
            handle.started_at = time.time()
            handle.state = "running"
            handle.progress_text = "выполняется"
            registry.start(handle.registry_task_id, progress_text=handle.progress_text)
            self.task_started.emit(handle.name)
            try:
                with registry.bind(handle.registry_task_id):
                    return _run_registered()
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                record_slow_operation("task." + handle.name, elapsed_ms, detail={"cancelled": bool(stop_check()), "state": handle.state})
                self.task_completed.emit(handle.name, float(elapsed_ms))
                signals.finished.emit()

        def _run_registered():
            try:
                if handle.cancelled():
                    handle.state = "cancelled"
                    handle.progress_text = "остановлено"
                    registry.cancel(handle.registry_task_id)
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
                    registry.cancel(handle.registry_task_id)
                    self.task_cancelled.emit(handle.name)
                else:
                    handle.state = "completed"
                    handle.progress_text = "завершено"
                    registry.finish(handle.registry_task_id)
                    signals.result.emit(res)
                return res
            except Exception as exc:
                if handle.cancelled():
                    handle.state = "cancelled"
                    handle.progress_text = "остановлено"
                    registry.cancel(handle.registry_task_id, detail=str(exc))
                    self.task_cancelled.emit(handle.name)
                    return None
                handle.state = "failed"
                handle.progress_text = "ошибка"
                text = traceback.format_exc()
                registry.fail(handle.registry_task_id, exc, detail=text[-1000:])
                self._logger.error("Background task failed: %s\n%s", name, text)
                self.task_failed.emit(handle.name, text)
                signals.error.emit(text)
                raise

        future = self.pool.submit(runner)
        handle.future = future

        def _future_done(done_future):
            if done_future.cancelled():
                handle.state = "cancelled"
                handle.progress_text = "остановлено"
                registry.cancel(handle.registry_task_id)
                self.task_cancelled.emit(handle.name)
                signals.finished.emit()

        future.add_done_callback(_future_done)
        signals.finished.connect(lambda _h=handle: self._forget_finished(_h))
        if future.done():
            self._forget_finished(handle)
        return handle

    def _forget_finished(self, handle):
        try:
            self.tasks = [task for task in self.tasks if task is not handle and not bool(task.future and task.future.done())]
        except Exception:
            pass


    def cancel_by_name(self, *names: str) -> int:
        """Cancel queued/running tasks with exact names. Returns number requested."""
        wanted = {str(n) for n in names if str(n)}
        if not wanted:
            return 0
        count = 0
        for task in list(self.tasks):
            try:
                if task.name in wanted and (task.future is None or not task.future.done()):
                    task.cancel()
                    self.task_cancelled.emit(task.name)
                    count += 1
            except Exception:
                pass
        return count

    def cancel_name_prefix(self, *prefixes: str) -> int:
        """Cancel queued/running tasks whose name starts with one of prefixes."""
        prefixes = tuple(str(p) for p in prefixes if str(p))
        if not prefixes:
            return 0
        count = 0
        for task in list(self.tasks):
            try:
                if str(task.name).startswith(prefixes) and (task.future is None or not task.future.done()):
                    task.cancel()
                    self.task_cancelled.emit(task.name)
                    count += 1
            except Exception:
                pass
        return count

    def active_snapshot(self):
        """Return process-wide task state, including parser and maintenance work."""
        try:
            return get_background_task_registry().active_snapshot(include_parents=True)
        except Exception:
            return []

    def recent_snapshot(self, limit: int = 100):
        try:
            return get_background_task_registry().recent_snapshot(limit)
        except Exception:
            return []

    def cancel_task_id(self, task_id: str) -> bool:
        try:
            return bool(get_background_task_registry().request_cancel(str(task_id or "")))
        except Exception:
            return False

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
