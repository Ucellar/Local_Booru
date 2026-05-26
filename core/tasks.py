"""Small background-task helpers for future module cleanup.

Existing pages still use their current QThread workers. New code should prefer this
wrapper so heavy work stays out of the UI thread and cleanup is predictable.
"""
from __future__ import annotations

from typing import Callable, Any

from PySide6.QtCore import QObject, QThread, Signal, Slot


class TaskWorker(QObject):
    progress = Signal(str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            self.result.emit(self.func(*self.args, **self.kwargs))
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


def run_in_thread(owner: QObject, func: Callable[..., Any], *args: Any, on_result=None, on_error=None, on_finished=None, **kwargs: Any):
    """Run func in QThread and attach objects to owner to avoid early GC."""
    thread = QThread(owner)
    worker = TaskWorker(func, *args, **kwargs)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    if on_result:
        worker.result.connect(on_result)
    if on_error:
        worker.error.connect(on_error)
    if on_finished:
        worker.finished.connect(on_finished)
    if not hasattr(owner, "_background_tasks"):
        owner._background_tasks = []
    owner._background_tasks.append((thread, worker))

    def _cleanup():
        try:
            owner._background_tasks.remove((thread, worker))
        except Exception:
            pass

    thread.finished.connect(_cleanup)
    thread.start()
    return thread, worker
