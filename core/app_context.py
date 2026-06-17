"""Lightweight application context / service container.

Purpose:
- keep settings access in one place;
- give pages one stable object for shared app services;
- avoid direct cross-imports between unrelated modules.

This is intentionally small. It is a compatibility layer, not an enterprise rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.settings import load_settings, save_settings
from core.task_manager import TaskManager


@dataclass
class AppContext:
    """Shared runtime state for UI pages and future modules."""

    settings: dict[str, Any] = field(default_factory=load_settings)
    _services: dict[str, Any] = field(default_factory=dict)

    def save_settings(self) -> None:
        save_settings(self.settings)

    @property
    def task_manager(self):
        def _make_task_manager():
            try:
                from core.local_parallel import local_workers
                workers = local_workers(self.settings, "task_max_workers", int(self.settings.get("local_background_workers", 4) or 4), maximum=16)
            except Exception:
                workers = self.settings.get("task_max_workers", 4)
            return TaskManager(max_workers=workers)
        return self.get_or_create("task_manager", _make_task_manager)

    def register(self, name: str, service: Any) -> Any:
        self._services[name] = service
        return service

    def get(self, name: str, default: Any = None) -> Any:
        return self._services.get(name, default)

    def get_or_create(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self._services:
            self._services[name] = factory()
        return self._services[name]
