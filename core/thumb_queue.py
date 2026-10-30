"""Pure-Python priority/cancellation scheduler for thumbnail work.

Qt owns only the workers that are actively running. Everything waiting stays in
this queue, so obsolete gallery generations can be removed safely without
QThreadPool.tryTake() or references to auto-deleted QRunnable objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from typing import Any, Hashable

PRIORITY_VISIBLE = 0
PRIORITY_CURRENT_PAGE = 10
PRIORITY_PREFETCH = 20
PRIORITY_BACKGROUND = 30


@dataclass
class ThumbnailInterest:
    priority: int
    callbacks: list[Any] = field(default_factory=list)


@dataclass
class ThumbnailRequest:
    key: tuple[str, int, int]
    interests: dict[tuple[str, Hashable | None], ThumbnailInterest] = field(default_factory=dict)
    state: str = "queued"  # queued | running
    effective_priority: int = PRIORITY_BACKGROUND
    queue_revision: int = 0
    sequence: int = 0


class ThumbnailWorkQueue:
    def __init__(self, *, max_active: int = 3, max_pending: int = 256):
        self.max_active = max(1, int(max_active or 1))
        self.max_pending = max(1, int(max_pending or 1))
        self.requests: dict[tuple[str, int, int], ThumbnailRequest] = {}
        self.active: set[tuple[str, int, int]] = set()
        self._heap: list[tuple[int, int, int, tuple[str, int, int]]] = []
        self._sequence = 0
        self.stopping = False

    @staticmethod
    def token(owner: str | None, generation: Hashable | None) -> tuple[str, Hashable | None]:
        name = str(owner or "").strip()
        if not name:
            return ("__anonymous__", None)
        try:
            hash(generation)
            stable = generation
        except Exception:
            stable = repr(generation)
        return (name, stable)

    @staticmethod
    def priority(value: int | None) -> int:
        try:
            return max(-1000, min(1000, int(value if value is not None else PRIORITY_BACKGROUND)))
        except Exception:
            return PRIORITY_BACKGROUND

    def add(
        self,
        key: tuple[str, int, int],
        *,
        owner: str | None = None,
        generation: Hashable | None = None,
        priority: int = PRIORITY_BACKGROUND,
        callback: Any = None,
    ) -> tuple[bool, list[tuple[str, int, int]]]:
        if self.stopping:
            return False, []
        token = self.token(owner, generation)
        priority = self.priority(priority)
        req = self.requests.get(key)
        if req is None:
            if len(self.requests) >= self.max_pending and not self._evict_for_priority(priority):
                return False, []
            req = ThumbnailRequest(key=key)
            self.requests[key] = req

        interest = req.interests.get(token)
        if interest is None:
            interest = ThumbnailInterest(priority=priority)
            req.interests[token] = interest
        else:
            interest.priority = min(int(interest.priority), priority)
        if callback is not None:
            interest.callbacks.append(callback)

        new_priority = min((item.priority for item in req.interests.values()), default=PRIORITY_BACKGROUND)
        if req.state == "queued" and (new_priority != req.effective_priority or req.queue_revision == 0):
            req.effective_priority = new_priority
            self._push(req)
        return True, self.dispatch()

    def cancel_key(
        self,
        key: tuple[str, int, int],
        *,
        owner: str | None = None,
        generation: Hashable | None = None,
    ) -> tuple[int, list[tuple[str, int, int]]]:
        req = self.requests.get(key)
        if req is None:
            return 0, []
        token = self.token(owner, generation)
        if token not in req.interests:
            return 0, []
        req.interests.pop(token, None)
        self._after_interest_change(req)
        return 1, self.dispatch()

    def cancel_owner(
        self,
        owner: str,
        generation: Hashable | None = None,
    ) -> tuple[int, list[tuple[str, int, int]]]:
        owner = str(owner or "").strip()
        if not owner:
            return 0, []
        removed = 0
        for req in list(self.requests.values()):
            tokens = [
                token for token in req.interests
                if token[0] == owner and (generation is None or token[1] == generation)
            ]
            for token in tokens:
                req.interests.pop(token, None)
                removed += 1
            if tokens:
                self._after_interest_change(req)
        return removed, self.dispatch()

    def complete(
        self,
        key: tuple[str, int, int],
    ) -> tuple[list[Any], bool, list[tuple[str, int, int]]]:
        req = self.requests.pop(key, None)
        self.active.discard(key)
        callbacks: list[Any] = []
        should_emit = False
        if req is not None and req.interests:
            should_emit = True
            for interest in req.interests.values():
                callbacks.extend(interest.callbacks)
        return callbacks, should_emit, self.dispatch()

    def dispatch(self) -> list[tuple[str, int, int]]:
        starts: list[tuple[str, int, int]] = []
        if self.stopping:
            return starts
        while len(self.active) < self.max_active and self._heap:
            priority, _sequence, revision, key = heapq.heappop(self._heap)
            req = self.requests.get(key)
            if req is None or req.state != "queued":
                continue
            if revision != req.queue_revision or priority != req.effective_priority:
                continue
            if not req.interests:
                self.requests.pop(key, None)
                continue
            req.state = "running"
            self.active.add(key)
            starts.append(key)
        return starts

    def configure(self, *, max_active: int | None = None, max_pending: int | None = None) -> list[tuple[str, int, int]]:
        if max_active is not None:
            self.max_active = max(1, int(max_active or 1))
        if max_pending is not None:
            self.max_pending = max(1, int(max_pending or 1))
        return self.dispatch()

    def stop_queued(self) -> None:
        self.stopping = True
        for key, req in list(self.requests.items()):
            req.interests.clear()
            if req.state == "queued":
                self.requests.pop(key, None)
        self._heap.clear()

    def pending_count(self) -> int:
        return len(self.requests)

    def queued_count(self) -> int:
        return sum(1 for req in self.requests.values() if req.state == "queued")

    def active_count(self) -> int:
        return len(self.active)

    def _after_interest_change(self, req: ThumbnailRequest) -> None:
        if not req.interests:
            if req.state == "queued":
                self.requests.pop(req.key, None)
            return
        new_priority = min(item.priority for item in req.interests.values())
        if req.state == "queued" and new_priority != req.effective_priority:
            req.effective_priority = new_priority
            self._push(req)

    def _evict_for_priority(self, incoming_priority: int) -> bool:
        candidates = [
            (req.effective_priority, req.sequence, key)
            for key, req in self.requests.items()
            if req.state == "queued"
        ]
        if not candidates:
            return False
        worst_priority = max(row[0] for row in candidates)
        if incoming_priority >= worst_priority:
            return False
        # Oldest request in the worst class is least valuable.
        _, _, victim = min((row for row in candidates if row[0] == worst_priority), key=lambda row: row[1])
        self.requests.pop(victim, None)
        return True

    def _push(self, req: ThumbnailRequest) -> None:
        self._sequence += 1
        req.sequence = self._sequence
        req.queue_revision += 1
        heapq.heappush(
            self._heap,
            (int(req.effective_priority), int(req.sequence), int(req.queue_revision), req.key),
        )
