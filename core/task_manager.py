from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any
import traceback

from PySide6.QtCore import QObject, Signal


class TaskSignals(QObject):
    progress = Signal(str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


@dataclass
class TaskHandle:
    name: str
    future: Future
    signals: TaskSignals
    _cancelled: bool = False

    def cancel(self):
        self._cancelled = True
        try: self.future.cancel()
        except Exception: pass

    def cancelled(self):
        return self._cancelled or self.future.cancelled()


class TaskManager(QObject):
    """Small central background task manager.

    It does not try to be enterprise. It gives every heavy operation one place
    for progress/result/error so UI pages don't create random QThreads.
    """

    def __init__(self, parent=None, max_workers=2):
        super().__init__(parent)
        try:
            from core.shutdown import register
            register("task manager", self.shutdown)
        except Exception:
            pass
        self.pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers or 2)), thread_name_prefix="local-booru")
        self.tasks = []

    def submit(self, func: Callable[..., Any], *args, name="task", on_progress=None, on_result=None, on_error=None, on_finished=None, **kwargs):
        signals = TaskSignals()
        if on_progress: signals.progress.connect(on_progress)
        if on_result: signals.result.connect(on_result)
        if on_error: signals.error.connect(on_error)
        if on_finished: signals.finished.connect(on_finished)

        handle_box = {}
        def progress(msg): signals.progress.emit(str(msg))
        def stop_check():
            h = handle_box.get("handle")
            return bool(h and h.cancelled())

        def runner():
            try:
                if "progress" in getattr(func, "__annotations__", {}) or "progress" in kwargs:
                    pass
                # Cooperative kwargs are accepted only if the function supports them.
                import inspect
                sig = inspect.signature(func)
                call_kwargs = dict(kwargs)
                if "progress" in sig.parameters and "progress" not in call_kwargs:
                    call_kwargs["progress"] = progress
                if "stop_check" in sig.parameters and "stop_check" not in call_kwargs:
                    call_kwargs["stop_check"] = stop_check
                res = func(*args, **call_kwargs)
                signals.result.emit(res)
                return res
            except Exception:
                text = traceback.format_exc()
                signals.error.emit(text)
                raise
            finally:
                signals.finished.emit()

        future = self.pool.submit(runner)
        handle = TaskHandle(name=name, future=future, signals=signals)
        handle_box["handle"] = handle
        self.tasks.append(handle)
        return handle

    def shutdown(self):
        for t in list(self.tasks):
            try: t.cancel()
            except Exception: pass
        try: self.pool.shutdown(wait=True, timeout=3, cancel_futures=True)
        except TypeError:
            try: self.pool.shutdown(wait=False)
            except Exception: pass
