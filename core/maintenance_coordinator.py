"""Cooperative admission control for rebuildable background maintenance.

The coordinator does not kill work mid-transaction.  Maintenance jobs call it
between bounded chunks and yield when foreground work becomes active.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import sys
import threading
import time
from typing import Callable, Iterator

from core.background_tasks import get_background_task_registry


@dataclass(frozen=True)
class MaintenanceDecision:
    allowed: bool
    reason: str = ""
    retry_after: float = 0.5


class MaintenanceCancelled(RuntimeError):
    pass


class MaintenanceCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._blockers: Counter[str] = Counter()
        self._paused = False
        self._last_user_activity = time.monotonic()
        self._active_maintenance_job = ""

    def note_user_activity(self) -> None:
        with self._condition:
            self._last_user_activity = time.monotonic()
            self._condition.notify_all()

    def set_paused(self, paused: bool) -> None:
        with self._condition:
            self._paused = bool(paused)
            self._condition.notify_all()

    @contextmanager
    def activity(self, name: str) -> Iterator[None]:
        key = str(name or "activity").strip() or "activity"
        with self._condition:
            self._blockers[key] += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                if self._blockers.get(key, 0) <= 1:
                    self._blockers.pop(key, None)
                else:
                    self._blockers[key] -= 1
                self._condition.notify_all()

    def blockers_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._blockers)

    @contextmanager
    def maintenance_job(
        self,
        name: str,
        *,
        stop_check: Callable[[], bool] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> Iterator[None]:
        """Serialise heavy maintenance and expose its wait/run lifecycle."""
        job_name = str(name or "maintenance").strip() or "maintenance"
        registry = get_background_task_registry()
        parent_id = registry.current_task_id()
        task_id = registry.register(
            f"maintenance:{job_name}",
            kind="maintenance",
            parent_id=parent_id or None,
            state="waiting",
            detail="waiting_for_maintenance_lane",
            progress_text="ожидает maintenance-полосу",
            cancellable=False,
        )
        last_reported = ""
        acquired = False
        try:
            with registry.bind(task_id):
                with self._condition:
                    while self._active_maintenance_job:
                        if stop_check and stop_check():
                            registry.cancel(task_id, detail="cancelled_while_waiting_for_maintenance_lane")
                            raise MaintenanceCancelled(f"{job_name}: cancelled while waiting for maintenance lane")
                        active = self._active_maintenance_job
                        registry.set_state(
                            task_id,
                            "waiting",
                            detail=f"maintenance_lane:{active}",
                            progress_text=f"ожидает завершения: {active}",
                        )
                        if progress and active != last_reported:
                            progress(f"Обслуживание ожидает завершения: {active}")
                            last_reported = active
                        self._condition.wait(timeout=0.5)
                    self._active_maintenance_job = job_name
                    acquired = True
                    self._condition.notify_all()
                registry.start(task_id, detail=job_name, progress_text="maintenance выполняется")
                yield
        except MaintenanceCancelled:
            if (registry.get(task_id) or {}).get("state") in {"queued", "running", "waiting", "paused", "cancelling"}:
                registry.cancel(task_id)
            raise
        except BaseException as exc:
            registry.fail(task_id, exc)
            raise
        else:
            registry.finish(task_id)
        finally:
            if acquired:
                with self._condition:
                    if self._active_maintenance_job == job_name:
                        self._active_maintenance_job = ""
                    self._condition.notify_all()

    def active_maintenance_job(self) -> str:
        with self._lock:
            return str(self._active_maintenance_job or "")

    def can_run(
        self,
        settings: dict | None,
        *,
        mode: str = "idle",
        min_idle_seconds: float = 5.0,
        min_available_mb: int = 1024,
    ) -> MaintenanceDecision:
        try:
            from core.shutdown import is_shutting_down
            if is_shutting_down():
                return MaintenanceDecision(False, "application_shutting_down", 0.2)
        except Exception:
            pass
        try:
            from core.database.connection import writes_blocked
            if writes_blocked():
                return MaintenanceDecision(False, "database_safe_mode", 1.0)
        except Exception:
            pass

        settings = settings or {}
        from core.parser.jobs import is_running as parser_running
        if parser_running() or bool(settings.get("_parser_running", False)):
            return MaintenanceDecision(False, "parser_active", 1.0)

        with self._lock:
            if self._paused:
                return MaintenanceDecision(False, "maintenance_paused", 1.0)
            blockers = dict(self._blockers)
            idle_for = max(0.0, time.monotonic() - self._last_user_activity)
        if blockers:
            name = sorted(blockers)[0]
            return MaintenanceDecision(False, f"activity:{name}", 0.5)

        if str(mode or "idle").lower() != "forced" and idle_for < max(0.0, float(min_idle_seconds or 0.0)):
            return MaintenanceDecision(False, "recent_user_activity", min(1.0, max(0.1, min_idle_seconds - idle_for)))

        try:
            from core.memory_guard import process_memory_snapshot
            snap = process_memory_snapshot()
            available = int(snap.get("available_bytes") or 0)
            load = float(snap.get("memory_load_percent") or 0.0)
            if available and available < int(min_available_mb) * 1024 * 1024:
                return MaintenanceDecision(False, "low_system_memory", 2.0)
            if load >= 95.0:
                return MaintenanceDecision(False, "high_system_memory_load", 2.0)
        except Exception:
            pass
        return MaintenanceDecision(True)

    def wait_for_permission(
        self,
        settings: dict | None,
        *,
        task_kind: str,
        mode: str = "idle",
        stop_check: Callable[[], bool] | None = None,
        progress: Callable[[str], None] | None = None,
        min_idle_seconds: float = 5.0,
    ) -> MaintenanceDecision:
        last_reason = None
        while True:
            if stop_check and stop_check():
                raise MaintenanceCancelled(f"{task_kind}: cancelled")
            decision = self.can_run(
                settings,
                mode=mode,
                min_idle_seconds=min_idle_seconds,
            )
            registry = get_background_task_registry()
            current_task_id = registry.current_task_id()
            if decision.allowed:
                if current_task_id:
                    registry.set_state(
                        current_task_id,
                        "running",
                        detail=str(task_kind),
                        progress_text=f"выполняется: {task_kind}",
                    )
                if last_reason and progress:
                    progress(f"Обслуживание продолжено: {task_kind}")
                return decision
            if current_task_id:
                registry.set_state(
                    current_task_id,
                    "waiting",
                    detail=str(decision.reason),
                    progress_text=f"ожидает: {decision.reason}",
                )
            if progress and decision.reason != last_reason:
                progress(f"Обслуживание ожидает: {decision.reason}")
            last_reason = decision.reason
            with self._condition:
                self._condition.wait(timeout=max(0.1, float(decision.retry_after or 0.5)))


_COORDINATOR = MaintenanceCoordinator()
_QT_ACTIVITY_FILTER = None


def get_maintenance_coordinator() -> MaintenanceCoordinator:
    return _COORDINATOR


def install_qt_activity_filter(app=None) -> bool:
    """Best-effort UI-idle tracking without coupling jobs to MainWindow.

    Windows only: on Linux and macOS a Python event filter on the whole application crashes Qt WebEngine (PySide6
    6.11 wraps a WebEngine-internal object for the filter, which re-enters the filter; a known Qt-for-Python
    problem, not seen on Windows), and both windows of the program show the web pages.  Without it nobody notes
    the person's activity there: every ``can_run(mode="idle")`` (the idle checkpoint, idle maintenance) takes the
    person as idle."""
    global _QT_ACTIVITY_FILTER
    if _QT_ACTIVITY_FILTER is not None:
        return True
    if sys.platform != "win32":
        return False
    try:
        from PySide6.QtCore import QObject, QEvent
        from PySide6.QtWidgets import QApplication

        class _ActivityFilter(QObject):
            def eventFilter(self, obj, event):  # noqa: N802 - Qt API
                try:
                    if event.type() in {
                        QEvent.MouseButtonPress,
                        QEvent.KeyPress,
                        QEvent.Wheel,
                        QEvent.TouchBegin,
                    }:
                        _COORDINATOR.note_user_activity()
                except Exception:
                    pass
                return False

        application = app or QApplication.instance()
        if application is None:
            return False
        _QT_ACTIVITY_FILTER = _ActivityFilter(application)
        application.installEventFilter(_QT_ACTIVITY_FILTER)
        return True
    except Exception:
        return False


def _reset_for_tests() -> None:
    global _COORDINATOR, _QT_ACTIVITY_FILTER
    _COORDINATOR = MaintenanceCoordinator()
    _QT_ACTIVITY_FILTER = None
