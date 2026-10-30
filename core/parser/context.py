"""Shared run context: settings, HTTP client, logging and UI activity hooks."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from core.parser.http import HttpClient


@dataclass
class ParserContext:
    settings: dict
    http: HttpClient
    log: Callable[[str], None]
    activity: Callable[[str, str, str], None] = lambda _label, _status, _path: None
    # tag-category memo shared by all workers: host -> {tag: category}
    tag_categories: dict = field(default_factory=dict)
    tag_categories_lock: threading.Lock = field(default_factory=threading.Lock)

    def setting_float(self, key: str, default: float, low: float, high: float) -> float:
        try:
            value = float(self.settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def setting_int(self, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self.settings.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def flag(self, key: str, default: bool) -> bool:
        value = self.settings.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", ""}
        return bool(value)
