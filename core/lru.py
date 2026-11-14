from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Generic, TypeVar
import weakref

K = TypeVar("K")
V = TypeVar("V")

_REGISTRY_LOCK = RLock()
_REGISTRY: dict[int, tuple[str, weakref.ReferenceType]] = {}


def _register_cache(cache, name: str) -> None:
    cache_id = id(cache)

    def _gone(_ref, *, _cache_id=cache_id):
        with _REGISTRY_LOCK:
            _REGISTRY.pop(_cache_id, None)

    try:
        ref = weakref.ref(cache, _gone)
    except TypeError:
        return
    with _REGISTRY_LOCK:
        _REGISTRY[cache_id] = (str(name or f"LRUCache@{cache_id:x}"), ref)


class LRUCache(OrderedDict, Generic[K, V]):
    """Small bounded LRU mapping with a dict-compatible API."""

    def __init__(self, maxsize: int = 1024, *args, name: str | None = None, **kwargs):
        self.maxsize = max(1, int(maxsize or 1))
        self._lock = RLock()
        self.cache_name = str(name or f"LRUCache@{id(self):x}")
        super().__init__()
        _register_cache(self, self.cache_name)
        if args or kwargs:
            self.update(*args, **kwargs)

    def __getitem__(self, key: K) -> V:
        with self._lock:
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def get(self, key: K, default=None):
        with self._lock:
            if key not in self:
                return default
            value = super().__getitem__(key)
            self.move_to_end(key)
            return value

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            super().__setitem__(key, value)
            self.move_to_end(key)
            while len(self) > self.maxsize:
                self.popitem(last=False)

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        with self._lock:
            for key, value in dict(*args, **kwargs).items():
                self[key] = value
