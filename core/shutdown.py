from __future__ import annotations

import logging
import threading
import time
import weakref
from typing import Callable, Iterable, Any

_log = logging.getLogger("local_booru.shutdown")
_callbacks: list[tuple[str, Callable[[], None]]] = []
_lock = threading.Lock()
_qthread_lock = threading.Lock()
_qthreads: list[tuple[str, weakref.ReferenceType[Any]]] = []
_shutting_down = False


def register(name: str, callback: Callable[[], None]) -> None:
    with _lock:
        _callbacks.append((str(name), callback))


def is_shutting_down() -> bool:
    return _shutting_down


def begin_shutdown() -> None:
    """Mark shutdown immediately without running callbacks."""
    global _shutting_down
    _shutting_down = True


def register_qthread(thread: Any, name: str | None = None) -> None:
    """Register a QThread-like object for bounded shutdown joins.

    The registry is weak-reference based so finished workers do not leak.  UI
    pages may also be scanned at exit, but explicit registration covers workers
    that are not QObject children of the main window.
    """
    if thread is None:
        return
    try:
        nm = str(name or getattr(thread, "objectName", lambda: "")() or thread.__class__.__name__)
    except Exception:
        nm = str(name or thread.__class__.__name__)
    try:
        ref = weakref.ref(thread)
    except TypeError:
        return
    with _qthread_lock:
        _qthreads.append((nm, ref))
    try:
        finished = getattr(thread, "finished", None)
        if finished is not None:
            finished.connect(lambda _nm=nm, _ref=ref: unregister_qthread_ref(_ref))
    except Exception:
        pass


def unregister_qthread_ref(ref: weakref.ReferenceType[Any]) -> None:
    with _qthread_lock:
        _qthreads[:] = [(n, r) for (n, r) in _qthreads if r is not ref]


def _looks_like_qthread(obj: Any) -> bool:
    return all(hasattr(obj, attr) for attr in ("isRunning", "requestInterruption", "wait"))


def _iter_obj_graph(obj: Any, seen: set[int], depth: int = 0):
    if obj is None or depth > 4:
        return
    oid = id(obj)
    if oid in seen:
        return
    seen.add(oid)
    yield obj
    # Qt object tree when available.
    try:
        for ch in list(obj.children() or []):
            yield from _iter_obj_graph(ch, seen, depth + 1)
    except Exception:
        pass
    # Python attributes of pages/main window.  Keep this deliberately shallow.
    try:
        vals = list(getattr(obj, "__dict__", {}).values())
    except Exception:
        vals = []
    for val in vals:
        if val is None:
            continue
        if isinstance(val, dict):
            for v in list(val.values())[:200]:
                yield from _iter_obj_graph(v, seen, depth + 1)
        elif isinstance(val, (list, tuple, set)):
            for v in list(val)[:200]:
                yield from _iter_obj_graph(v, seen, depth + 1)
        elif _looks_like_qthread(val) or hasattr(val, "children"):
            yield from _iter_obj_graph(val, seen, depth + 1)


def collect_qthreads(roots: Iterable[Any] | None = None) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    seen_ids: set[int] = set()
    with _qthread_lock:
        registered = list(_qthreads)
    for name, ref in registered:
        obj = ref()
        if obj is None:
            continue
        if id(obj) in seen_ids:
            continue
        if _looks_like_qthread(obj):
            seen_ids.add(id(obj))
            out.append((name, obj))
    for root in list(roots or []):
        for obj in _iter_obj_graph(root, set()):
            if id(obj) in seen_ids:
                continue
            if _looks_like_qthread(obj):
                try:
                    name = str(getattr(obj, "objectName", lambda: "")() or obj.__class__.__name__)
                except Exception:
                    name = obj.__class__.__name__
                seen_ids.add(id(obj))
                out.append((name, obj))
    return out


def join_qthreads(timeout_budget: float = 15.0, roots: Iterable[Any] | None = None) -> dict:
    """Request interruption and join all known QThreads within one budget."""
    deadline = time.monotonic() + max(0.0, float(timeout_budget or 0.0))
    threads = collect_qthreads(roots)
    running: list[tuple[str, Any]] = []
    for name, th in threads:
        try:
            if th.isRunning():
                running.append((name, th))
        except Exception:
            pass
    for name, th in running:
        try:
            th.requestInterruption()
        except Exception as exc:
            _log.warning("Could not request interruption for %s: %s", name, exc)
    alive: list[str] = []
    for name, th in running:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            alive.append(name)
            continue
        try:
            ok = bool(th.wait(int(remaining * 1000)))
        except TypeError:
            # Some wrappers expose wait() without timeout.
            try:
                ok = bool(th.wait())
            except Exception:
                ok = False
        except Exception as exc:
            _log.warning("QThread wait failed for %s: %s", name, exc)
            ok = False
        try:
            if not ok and th.isRunning():
                alive.append(name)
        except Exception:
            if not ok:
                alive.append(name)
    if alive:
        _log.warning("Shutdown join budget exhausted; still running: %s", ", ".join(alive))
    return {"ok": not alive, "alive": alive, "joined": len(running) - len(alive), "seen": len(threads)}


def request_shutdown(timeout_hint: float = 15.0, qt_roots: Iterable[Any] | None = None) -> dict:
    begin_shutdown()
    with _lock:
        callbacks = list(reversed(_callbacks))
        _callbacks.clear()
    for name, cb in callbacks:
        try:
            _log.info("Stopping %s", name)
            cb()
        except Exception as e:
            _log.warning("Shutdown callback %s failed: %s", name, e)
    join_result = join_qthreads(timeout_hint, roots=qt_roots)
    if join_result.get("ok"):
        try:
            from core.database.connection import close_pooled_connections
            close_pooled_connections()
        except Exception:
            pass
    else:
        _log.warning("Skipping pooled SQLite close because worker threads are still alive")
    return join_result


def _reset_for_tests() -> None:
    global _shutting_down
    with _lock:
        _callbacks.clear()
    with _qthread_lock:
        _qthreads.clear()
    _shutting_down = False
