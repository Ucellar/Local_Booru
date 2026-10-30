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


def lru_diagnostics() -> list[dict]:
    """Return aggregate sizes for all live named LRU caches.

    Multiple Tagger instances can own caches with the same logical name.  They
    are aggregated so the RAM-guard log remains compact but still shows both
    total entries and instance count.
    """
    live = []
    stale = []
    with _REGISTRY_LOCK:
        for cache_id, (name, ref) in list(_REGISTRY.items()):
            cache = ref()
            if cache is None:
                stale.append(cache_id)
                continue
            try:
                with cache._lock:
                    live.append((name, len(cache), int(cache.maxsize)))
            except Exception:
                live.append((name, -1, int(getattr(cache, "maxsize", 0) or 0)))
        for cache_id in stale:
            _REGISTRY.pop(cache_id, None)
    aggregated: dict[str, dict] = {}
    for name, entries, maxsize in live:
        row = aggregated.setdefault(name, {
            "name": name,
            "instances": 0,
            "entries": 0,
            "maxsize_total": 0,
        })
        row["instances"] += 1
        if entries >= 0:
            row["entries"] += entries
        row["maxsize_total"] += max(0, maxsize)
    return [aggregated[key] for key in sorted(aggregated)]


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

    def set_maxsize(self, maxsize: int) -> None:
        with self._lock:
            self.maxsize = max(1, int(maxsize or 1))
            while len(self) > self.maxsize:
                self.popitem(last=False)
