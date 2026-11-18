"""Ordered foreground downloads for the Steam appearance.

The individual downloaders still own their files and pause/resume behavior. A transfer of the active slot requests
their ordinary cooperative stop, waits for the worker to finish, then starts the selected job. Existing partial files
are reused by the same downloader when the previous job returns to the front.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from core import page_jobs, shutdown
from core.file_safety import read_json_kept, write_json_kept

_LOG = logging.getLogger("local_booru.download_scheduler")


@dataclass(frozen=True)
class Adapter:
    task_kind: str
    restore: Callable
    legacy_busy: Callable = lambda: False


@dataclass
class _Entry:
    id: str
    kind: str
    label: str
    record: dict | None
    presentation: dict
    dedupe: str
    work: Callable | None = None
    about: object = None
    paused: bool = False

    def kept(self) -> dict:
        presentation = {k: v for k, v in self.presentation.items() if k != "find"}
        return {"id": self.id, "kind": self.kind, "label": self.label, "record": self.record,
                "presentation": presentation, "dedupe": self.dedupe, "paused": self.paused}


class Scheduler:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()
        self._enabled = False
        self._adapters: dict[str, Adapter] = {}
        self._active: _Entry | None = None
        self._next: list[_Entry] = []
        self._later: list[_Entry] = []
        self._worker: tuple[_Entry, page_jobs.PageTask] | None = None
        self._revision = secrets.randbits(40)
        self._error = ""
        self._retry_at = 0.0
        self._loaded = False
        self._dirty = False
        self._load()

    def _load(self) -> bool:
        if self._loaded:
            return True
        try:
            kept = read_json_kept(self._path, dict, what="foreground downloads") or {}
        except OSError as exc:
            self._io_error("Не удалось прочитать очередь загрузок", exc)
            return False
        self._set_order(self._read_entry(kept.get("active")), self._read_list(kept.get("next")),
                        self._read_list(kept.get("later")))
        self._loaded = True
        self._error = ""
        return True

    @staticmethod
    def _read_entry(value) -> _Entry | None:
        if not isinstance(value, dict) or not isinstance(value.get("record"), dict):
            return None
        if not all(isinstance(value.get(key), str) and value[key] for key in ("id", "kind", "label")):
            return None
        public = value.get("presentation")
        return _Entry(value["id"], value["kind"], value["label"], value["record"],
                      public if isinstance(public, dict) else {}, str(value.get("dedupe") or ""),
                      paused=bool(value.get("paused")))

    def _read_list(self, values) -> list[_Entry]:
        return [entry for value in values if (entry := self._read_entry(value)) is not None] \
            if isinstance(values, list) else []

    def _io_error(self, words: str, exc: OSError) -> None:
        self._error = words
        self._retry_at = time.monotonic() + 5.0
        _LOG.warning("%s: %s", words, exc)

    def _set_order(self, active: _Entry | None, queued: list[_Entry], postponed: list[_Entry]) -> None:
        """A drag stays valid when only the worker or item details change."""
        if (active.id if active else None) != (self._active.id if self._active else None) or \
                [entry.id for entry in queued] != [entry.id for entry in self._next] or \
                [entry.id for entry in postponed] != [entry.id for entry in self._later]:
            self._revision += 1
        self._active, self._next, self._later = active, queued, postponed

    def _commit(self, active: _Entry | None, queued: list[_Entry], postponed: list[_Entry]) -> bool:
        data = {"active": active.kept() if active is not None and active.record is not None else None,
                "next": [entry.kept() for entry in queued if entry.record is not None],
                "later": [entry.kept() for entry in postponed if entry.record is not None]}
        try:
            write_json_kept(self._path, json.dumps(data, ensure_ascii=False), dict)
        except OSError as exc:
            self._io_error("Не удалось сохранить очередь загрузок", exc)
            return False
        self._set_order(active, queued, postponed)
        self._dirty = False
        self._error = ""
        self._retry_at = 0.0
        return True

    def configure(self, enabled: bool, adapters: dict[str, Adapter] | None = None) -> None:
        with self._lock:
            if adapters is not None:
                self._adapters = dict(adapters)
                for adapter in adapters.values():
                    page_jobs.on_end(adapter.task_kind, self._ended)
            if self._enabled != bool(enabled):
                self._enabled = bool(enabled)
                self._revision += 1
            self._kick()

    def _kick(self) -> None:
        if self._worker is not None or shutdown.is_shutting_down():
            return
        if not self._adapters or not self._load():
            return
        if self._dirty and not self._commit(self._active, self._next, self._later):
            return
        if not (self._active is not None or self._next):
            return
        kinds = self._adapters.values()
        if any(adapter.legacy_busy() for adapter in kinds):
            return
        if any((task := page_jobs.current(adapter.task_kind)) is not None and task.running for adapter in kinds):
            return
        if self._active is None:
            if not self._commit(self._next[0], self._next[1:], self._later):
                return
        entry = self._active
        adapter = self._adapters.get(entry.kind)
        if adapter is None:
            self._error = "Неизвестный тип загрузки в очереди"
            return

        started_work = entry.work

        def run(say, stop_check):
            if stop_check():
                return {"stopped": True}
            work = started_work
            if work is None:
                work = adapter.restore(dict(entry.record or {}, paused=entry.paused))
            return work(say, stop_check)

        try:
            task = page_jobs.start(adapter.task_kind, entry.label, run, about=entry.about)
        except page_jobs.TaskBusy:
            return
        self._worker = entry, task

    def _ended(self, task: page_jobs.PageTask) -> None:
        with self._lock:
            if self._worker is not None and self._worker[1] is task:
                ended = self._worker[0]
                self._worker = None
                if shutdown.is_shutting_down():
                    return
                # A second move may have selected the same worker's entry again while it was stopping.
                if self._active is ended:
                    if not (task.status == "stopped" and getattr(task, "download_handover", False)):
                        self._set_order(None, self._next, self._later)
                        self._dirty = True
                elif task.status == "done" and not (isinstance(task.result, dict) and task.result.get("stopped")):
                    revision = self._revision
                    self._set_order(self._active, [entry for entry in self._next if entry is not ended],
                                    [entry for entry in self._later if entry is not ended])
                    self._dirty = self._dirty or self._revision != revision
            self._kick()

    def submit(self, kind: str, label: str, work: Callable | None, *, record: dict | None = None,
               presentation: dict | None = None, about=None, dedupe: str | None = None,
               replace_waiting: bool = True, dedupe_active: bool = True) -> dict | None:
        """Return None for the traditional queue, or the usual id / queued response plus a durable queue id."""
        with self._lock:
            if not self._accepting(kind):
                return None
            if not self._load():
                return {"ok": False, "error": self._error}
            match = next((entry for entry in (self._active if dedupe_active else None, *self._next, *self._later)
                          if entry is not None and entry.kind == kind and dedupe and entry.dedupe == dedupe), None)
            was_new = match is None
            if match is None:
                match = _Entry(secrets.token_hex(12), kind, label, record, dict(presentation or {}), dedupe or "",
                               work, about, bool((record or {}).get("paused")))
                if not self._commit(self._active, [*self._next, match], self._later):
                    return {"ok": False, "error": self._error}
            elif replace_waiting and self._worker_entry() is not match:
                if kind == "manhwa" and match.record is not None and record is not None:
                    old, new = match.record.get("chapters"), record.get("chapters")
                    chapters = None if old is None or new is None else list(dict.fromkeys([*old, *new]))
                    if chapters != new:
                        record = dict(record, chapters=chapters, retry=bool(record.get("retry")) and chapters is not None)
                        presentation = dict(presentation or {}, chosen=list(chapters or []),
                                            count=len(chapters) if chapters is not None else
                                            max(match.presentation.get("count", 0), (presentation or {}).get("count", 0)))
                        work = None  # restore the combined selection; the original callable only has the new chapters
                updated = replace(match, label=label, record=record, presentation=dict(presentation or {}),
                                  work=work, about=about)
                if not self._commit(updated if self._active is match else self._active,
                                    [updated if entry is match else entry for entry in self._next],
                                    [updated if entry is match else entry for entry in self._later]):
                    return {"ok": False, "error": self._error}
                match = updated
            self._kick()
            return self._admission(match, not replace_waiting and not was_new)

    def submit_galleries(self, galleries: list[tuple[int, str, str, Callable]]) -> dict | None:
        """Commit the requested galleries together, each with its own place in the queue."""
        with self._lock:
            if not galleries or not self._accepting("manga"):
                return None
            if not self._load():
                return {"ok": False, "error": self._error}
            known = {entry.dedupe: entry for entry in (self._active, *self._next, *self._later)
                     if entry is not None and entry.kind == "manga"}
            first = known.get(str(galleries[0][0]))
            queued = list(self._next)
            for number, title, label, work in galleries:
                key = str(number)
                if key not in known:
                    known[key] = _Entry(secrets.token_hex(12), "manga", label, {"number": number},
                                        {"number": number, "title": title, "count": 0, "hentai": True}, key, work)
                    queued.append(known[key])
            if len(queued) != len(self._next) and not self._commit(self._active, queued, self._later):
                return {"ok": False, "error": self._error}
            self._kick()
            return self._admission(known[str(galleries[0][0])], first is not None)

    def _accepting(self, kind: str) -> bool:
        return kind in self._adapters and bool(self._enabled or self._active or self._next or self._worker)

    def _admission(self, entry: _Entry, already: bool) -> dict:
        if self._worker_entry() is entry:
            return {"id": self._worker[1].id, "queue_id": entry.id, "already": already}
        if entry in self._later:
            return {"queued": self._later.index(entry) + 1, "queue_id": entry.id, "postponed": True}
        return {"queued": self._next.index(entry) + 1 if entry in self._next else 1, "queue_id": entry.id}

    def _worker_entry(self) -> _Entry | None:
        return self._worker[0] if self._worker is not None else None

    def _view(self, entry: _Entry, lane: str) -> dict:
        task_id = self._worker[1].id if self._worker_entry() is entry else None
        return {**entry.presentation, "title": entry.presentation.get("title") or entry.label,
                "id": entry.id, "kind": entry.kind, "jobId": task_id, "lane": lane,
                "paused": entry.paused, "durable": entry.record is not None}

    def snapshot(self) -> dict:
        with self._lock:
            if self._retry_at and time.monotonic() >= self._retry_at:
                self._retry_at = 0.0
                self._kick()
            worker = self._worker_entry()
            return {"enabled": self._enabled, "revision": self._revision, "error": self._error,
                    "active": self._view(self._active, "active") if self._active is not None else None,
                    "worker": self._view(worker, "active") if worker is not None else None,
                    "next": [self._view(entry, "next") for entry in self._next],
                    "later": [self._view(entry, "later") for entry in self._later],
                    "switching": worker is not None and worker is not self._active}

    def waiting(self, kind: str) -> list[tuple[dict, dict]]:
        """Native source pages need their queue ids, plus the original options when adding to the same title."""
        with self._lock:
            return [(self._view(entry, lane), dict(entry.record or {}))
                    for lane, entries in (("next", self._next), ("later", self._later))
                    for entry in entries if entry.kind == kind]

    def _interrupt(self) -> None:
        if self._worker is not None:
            entry, task = self._worker
            if entry is not self._active and task.running:
                task.download_handover = any(entry is item for item in (*self._next, *self._later))
                task.stop()

    def move(self, wanted: str, lane: str, before: str | None, revision: int) -> bool:
        with self._lock:
            if not self._enabled or revision != self._revision or lane not in ("active", "next", "later"):
                return False
            entry = next((item for item in (self._active, *self._next, *self._later)
                          if item is not None and item.id == wanted), None)
            if entry is None or (lane == "later" and entry.record is None):
                return False
            active, queued, postponed = self._active, list(self._next), list(self._later)
            destination = queued if lane == "next" else postponed
            if before is not None and (lane == "active" or not any(item.id == before for item in destination)):
                return False
            if before == wanted or (lane == "active" and entry is self._active):
                return True
            if entry is active:
                active = None
            elif entry in queued:
                queued.remove(entry)
            else:
                postponed.remove(entry)
            prior = active if lane == "active" else None
            if lane == "active":
                if prior is not None:
                    queued.insert(0, prior)
                active = entry
            else:
                at = next((i for i, item in enumerate(destination) if item.id == before), len(destination))
                destination.insert(at, entry)
            was_paused = entry.paused
            prior_paused = prior.paused if prior is not None else False
            entry.paused = False
            if prior is not None:
                prior.paused = False
            if not self._commit(active, queued, postponed):
                entry.paused = was_paused
                if prior is not None:
                    prior.paused = prior_paused
                return False
            if lane == "later" or self._worker_entry() is entry:
                self._refresh_video(entry)
            if prior is not None:
                self._refresh_video(prior)
            self._interrupt()
            self._kick()
            return True

    @staticmethod
    def _refresh_video(entry: _Entry) -> None:
        if entry.kind == "video" and entry.record is not None:
            entry.work = None

    def remove(self, wanted: str, *, waiting_kind: str | None = None) -> bool:
        with self._lock:
            active = self._active
            entry = next((item for item in (active, *self._next, *self._later)
                          if item is not None and item.id == wanted), None)
            if entry is None or (waiting_kind is not None and (entry is active or entry.kind != waiting_kind)):
                return False
            if not self._commit(None if active is entry else active,
                                [item for item in self._next if item is not entry],
                                [item for item in self._later if item is not entry]):
                return False
            self._interrupt()
            self._kick()
            return True

    def stop_kind(self, kind: str, task) -> bool | None:
        with self._lock:
            if self._worker is None or self._worker[1] is not task or self._worker_entry().kind != kind:
                return None
            return self.remove(self._worker_entry().id)

    def stop_all(self) -> int:
        with self._lock:
            count = int(self._active is not None) + len(self._next)
            if count and self._commit(None, [], self._later):
                self._interrupt()
                return count
            return 0

    def note_paused(self, kind: str, paused: bool, task=None) -> None:
        with self._lock:
            entry = self._worker_entry()
            if entry is not self._active or entry is None or entry.kind != kind or entry.paused == bool(paused):
                return
            if task is not None and self._worker[1] is not task:
                return
            entry.paused = bool(paused)
            if not self._commit(self._active, self._next, self._later):
                self._dirty = True


_INSTANCE: Scheduler | None = None
_INSTANCE_LOCK = threading.Lock()


def manager() -> Scheduler:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            from core.paths import SETTINGS_DIR
            _INSTANCE = Scheduler(Path(SETTINGS_DIR) / "download_schedule.json")
        return _INSTANCE


def configure(enabled: bool, adapters: dict[str, Adapter] | None = None) -> None:
    manager().configure(enabled, adapters)


def submit(kind: str, label: str, work: Callable | None, **options) -> dict | None:
    return manager().submit(kind, label, work, **options) if _INSTANCE is not None else None


def submit_galleries(galleries: list[tuple[int, str, str, Callable]]) -> dict | None:
    return manager().submit_galleries(galleries) if _INSTANCE is not None else None


def snapshot() -> dict:
    return manager().snapshot()


def waiting(kind: str) -> list[tuple[dict, dict]]:
    return manager().waiting(kind) if _INSTANCE is not None else []


def move(wanted: str, lane: str, before: str | None, revision: int) -> bool:
    return manager().move(wanted, lane, before, revision)


def remove(wanted: str, *, waiting_kind: str | None = None) -> bool:
    return manager().remove(wanted, waiting_kind=waiting_kind) if _INSTANCE is not None and isinstance(wanted, str) else False


def stop_kind(kind: str, task) -> bool | None:
    return manager().stop_kind(kind, task) if _INSTANCE is not None else None


def stop_all() -> int:
    return manager().stop_all() if _INSTANCE is not None else 0


def note_paused(kind: str, paused: bool, task=None) -> None:
    if _INSTANCE is not None:
        manager().note_paused(kind, paused, task)
