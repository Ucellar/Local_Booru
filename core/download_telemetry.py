"""One-second network and application file-write rates for download page tasks.

Record received media payload and successful writes at the I/O points. Samples omit
the current partial second and use the monotonic clock even if the wall clock changes.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import monotonic, time

_KEEP_SECONDS = 120
_KEEP_TASKS = 32
_WALL_OFFSET_MS = round(1000 * (time() - monotonic()))
_CURRENT: ContextVar[tuple[int, int] | None] = ContextVar("download_telemetry", default=None)
_LOCK = Lock()


class _Transfer:
    def __init__(self, second: int):
        self.first = self.second = second
        self.net = self.disk = self.max_net = self.max_disk = 0
        self.browser_reads = 0
        self.net_unknown = False
        self.key: int | None = None
        self.samples: deque[tuple[int, int, int, int | None, bool]] = deque(maxlen=_KEEP_SECONDS)

    def advance(self, second: int) -> None:
        if second <= self.second:
            return
        self.samples.append((self.second, self.net, self.disk, self.key, self.net_unknown))
        if not self.net_unknown:
            self.max_net = max(self.max_net, self.net)
        self.max_disk = max(self.max_disk, self.disk)
        if self.browser_reads:
            for missing in range(max(self.second + 1, second - _KEEP_SECONDS), second):
                self.samples.append((missing, 0, 0, self.key, True))
        else:
            self.key = None
        self.second = second
        self.net = self.disk = 0
        self.net_unknown = bool(self.browser_reads)

    def attribute(self, key: int) -> None:
        if not (self.net or self.disk or self.net_unknown):
            self.key = key
        elif self.key != key:
            self.key = None


_TRANSFERS: OrderedDict[int, _Transfer] = OrderedDict()


def _recording(task_id: int) -> _Transfer:
    """Get or initialize the history for a writer inside `_LOCK`."""
    second = int(monotonic())
    transfer = _TRANSFERS.get(task_id)
    if transfer is None:
        transfer = _TRANSFERS[task_id] = _Transfer(second)
        if len(_TRANSFERS) > _KEEP_TASKS:
            _TRANSFERS.popitem(last=False)
    else:
        _TRANSFERS.move_to_end(task_id)
    transfer.advance(second)
    return transfer


@contextmanager
def track(task_id: int):
    """Scope the media I/O to its PageTask, including any copied worker context."""
    token = _CURRENT.set((task_id, task_id))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def title(key: int) -> None:
    """Use the current gallery's ID for samples of a task that downloads several galleries."""
    current = _CURRENT.get()
    if current is not None:
        _CURRENT.set((current[0], key))


@contextmanager
def browser_read(picture: bool):
    """Mark media reads whose browser returns the entire response body at once."""
    current = _CURRENT.get()
    if current is None or not picture:
        yield
        return
    task_id, key = current
    with _LOCK:
        transfer = _recording(task_id)
        transfer.attribute(key)
        transfer.browser_reads += 1
        transfer.net_unknown = True
    try:
        yield
    finally:
        with _LOCK:
            transfer.advance(int(monotonic()))
            transfer.browser_reads -= 1


def record(*, net: int = 0, disk: int = 0) -> None:
    """Count transfer payload bytes in the current task, ignoring other library I/O."""
    current = _CURRENT.get()
    if current is None or not (net or disk):
        return
    task_id, key = current
    with _LOCK:
        transfer = _recording(task_id)
        transfer.attribute(key)
        transfer.net += net
        transfer.disk += disk


def snapshot(task_id: int | None) -> dict:
    """Return a rolling history with rates in bytes/s and timestamps in Unix milliseconds."""
    with _LOCK:
        transfer = _TRANSFERS.get(task_id) if task_id is not None else None
        if transfer is None:
            return {"net": 0, "disk": 0, "max_net": 0, "max_disk": 0, "net_unknown": False, "samples": []}
        end = int(monotonic())
        transfer.advance(end)
        start = max(transfer.first, end - _KEEP_SECONDS)
        rates = {sample[0]: sample[1:] for sample in transfer.samples if sample[0] >= start}
        max_net, max_disk = transfer.max_net, transfer.max_disk
        net_reading = transfer.net_unknown
    missing = (0, 0, None, False)
    points = []
    for second in range(start, end):
        net, disk, key, net_unknown = rates.get(second, missing)
        points.append({"at": _WALL_OFFSET_MS + second * 1000, "net": net, "disk": disk, "key": key,
                       "net_unknown": net_unknown})
    net, disk, _, net_unknown = rates.get(end - 1, missing)
    return {"net": net, "disk": disk, "max_net": max_net, "max_disk": max_disk,
            "net_unknown": net_unknown or net_reading, "samples": points}
