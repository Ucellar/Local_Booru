"""(v5.44.74) How a download of «Скачать манхву» goes: chapter after chapter with «Пауза», a chapter that failed asked
again, and what the page shows of it.

The owner, 5.11.2026: «В манхве можно было бы сделать скачивание и красивее и с паузой и если глава выдала ошибку
перепроверить 3 раза», «Лучше посмотри как сделано скачивание видео» — so as «Скачать с сайта» does it
(``core/video_download.py``):

* «Пауза» (:func:`pause` / :func:`resume`) — the chapter going stops where it is: the strips on their way come (a
  strip is a few hundred kilobytes — none is thrown away to be asked again), the next ones are not asked; the strips
  there stay in ``.lb/partial``, the download waits; «Продолжить» goes on with the same chapter from the strip it
  stopped at;
* a chapter that did not come (a site's error, a strip that broke off, a file not written) is asked again
  ``RETRY_WAITS`` times, each after its wait — the chapters after it go on meanwhile, and its turn comes as soon as its
  wait is over (before the chapters after it); another site's copy of it (``Take.alt``) is asked at once each time;
  a paid chapter (``manhwa_sources.Paid``) and a site the window's browser does not get through (``Closed``) are not
  asked again; ``GIVE_UP_AFTER`` chapters in a row that did not come, each asked again too, — the site gives none now:
  the rest wait for the next time (the night, or «Скачать» again);
* the meter (:func:`meter`): each chapter's state for its tile («wait», «going», «paused», «retry», «done»,
  «failed»), the strips of the one going, the bytes, the speed, the time left; ``run`` — the download (another one —
  the page forgets the tiles it had), ``rev`` — the newest change: the page asks only for what changed after it (a
  title has hundreds of chapters, the page asks every second);
* (v5.44.120) «Пропустить главу» (:func:`skip`, «Загрузки»): the chapter going (or paused) stops where it is — its strips
  there stay in ``.lb/partial`` for the next time — and the download goes on with the next one; the chapter skipped is
  one that did not come (``failed``, «пропущена»: «Повторить не скачавшееся» of «Готово» takes it again), not one of
  the chapters in a row that make it give up.
"""
from __future__ import annotations

import threading
import time
from collections import deque

RETRY_WAITS = (5.0, 15.0, 30.0)     # a chapter that did not come is asked again after these (the owner: «3 раза»)
GIVE_UP_AFTER = 5                   # chapters in a row that did not come, each asked again: the site gives none now
SPEED_SPAN = 8.0                    # seconds the speed is counted over
SKIPPED = "пропущена"               # (v5.44.120) why a chapter skipped by «Пропустить главу» did not come

_PAUSE = threading.Event()          # «Пауза» of the download going (one at a time)
_SKIP = threading.Event()           # (v5.44.120) «Пропустить главу»: the chapter going (or paused) is left for now
_LOCK = threading.Lock()
_STATE: dict = {"run": 0, "rev": 0, "items": []}
_SAMPLES: deque = deque()           # (when, bytes so far) of the strips that came: the speed
_COUNT = {"run": 0, "rev": 0}


def pause() -> None:
    _PAUSE.set()


def resume() -> None:
    _PAUSE.clear()


def paused() -> bool:
    return _PAUSE.is_set()


def skip() -> bool:
    """(v5.44.120) «Пропустить главу»: the chapter going or paused now is left (the download goes on with the next).
    False — no chapter is going (nothing to skip)."""
    with _LOCK:
        going = any(x.get("state") in ("going", "paused") for x in _STATE.get("items") or []) \
            and not _STATE.get("finished")
    if going:
        _SKIP.set()
    return going


def _bump() -> int:
    _COUNT["rev"] += 1
    _STATE["rev"] = _COUNT["rev"]
    return _COUNT["rev"]


def begin(title: str, site: str, folder: str, takes: list, kind: str = "") -> None:
    """A new download: its chapters (``manhwa_download.Take``) waiting, nothing counted yet; ``kind`` — (v5.44.87) the
    folder it goes to («manhwa» / «manga»: «Загрузки» of «Скачать → По ссылке» names it)."""
    _SKIP.clear()                           # (v5.44.120) a skip asked of the one before is not this one's
    with _LOCK:
        _COUNT["run"] += 1
        _STATE.clear()
        rev = _bump()
        _STATE.update(run=_COUNT["run"], title=title, site=site, folder=folder, kind=kind, total=len(takes), bytes=0,
                      started=time.monotonic(), paused_for=0.0, finished=False, gave_up="",
                      items=[{"k": k, "id": t.id, "label": t.label, "name": t.name,
                              "from": t.source.label if t.helped and t.source is not None else "", "state": "wait",
                              "pages": 0, "got": 0, "why": "", "tries": 0, "again_at": 0.0, "rev": rev}
                             for k, t in enumerate(takes)])
        _SAMPLES.clear()


def item(k: int, **values) -> None:
    """One chapter's change (its state, its strips, why it did not come)."""
    with _LOCK:
        items = _STATE.get("items") or []
        if 0 <= k < len(items):
            items[k].update(values)
            items[k]["rev"] = _bump()


def values(**changes) -> None:
    """The download's own values (it ended, it gave up…)."""
    with _LOCK:
        _STATE.update(changes)
        _bump()


def add_bytes(count: int) -> None:
    """A strip came: its bytes for the speed."""
    now = time.monotonic()
    with _LOCK:
        _STATE["bytes"] = int(_STATE.get("bytes") or 0) + int(count)
        _SAMPLES.append((now, _STATE["bytes"]))
        while len(_SAMPLES) > 2 and _SAMPLES[1][0] < now - SPEED_SPAN:
            _SAMPLES.popleft()                      # the first one kept is the last before the span: its start


def _public(x: dict, now: float) -> dict:
    out = {k: v for k, v in x.items() if k not in ("again_at", "rev")}
    if x.get("state") == "retry" and x.get("again_at"):
        out["again"] = max(0, int(round(x["again_at"] - now)))
    return out


def meter(since: int = 0, run: int = 0) -> tuple:
    """(the download as the page shows it, the chapters changed after ``since`` — every one when ``run`` is not this
    download's).  The download: run, rev, title, total, done, failed, retrying, left, now (the chapter going or
    paused: label, strips), bytes, speed (bytes a second, over the last ``SPEED_SPAN``), eta (seconds, once two
    chapters came), paused, finished, gave_up."""
    now = time.monotonic()
    with _LOCK:
        state = {k: v for k, v in _STATE.items() if k not in ("items", "started", "paused_for")}
        items = _STATE.get("items") or []
        fresh = run != _STATE.get("run") or since <= 0
        changed = [_public(x, now) for x in items if fresh or int(x.get("rev") or 0) > since]
        counts: dict = {}
        for x in items:
            counts[x["state"]] = counts.get(x["state"], 0) + 1
        going = next((x for x in items if x["state"] in ("going", "paused")), None)
        speed = 0
        if len(_SAMPLES) > 1 and not _PAUSE.is_set():
            start_at, start_bytes = _SAMPLES[0]
            speed = int(max(0, (_STATE.get("bytes") or 0) - start_bytes) / max(1.0, now - start_at))
        active = now - float(_STATE.get("started") or now) - float(_STATE.get("paused_for") or 0.0)
    done = counts.get("done", 0)
    left = sum(counts.get(s, 0) for s in ("wait", "going", "paused", "retry"))
    state.update(done=done, failed=counts.get("failed", 0), retrying=counts.get("retry", 0), left=left,
                 speed=speed, paused=_PAUSE.is_set(),
                 now=_public(going, now) if going is not None else None,
                 eta=int(active / done * left) if done >= 2 and left and not state.get("finished") else 0)
    return state, changed


# ── the run ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _settle(ks) -> None:
    """The chapters left when the download ends half-way: waiting again, not «повтор» or «пауза» on their tiles."""
    with _LOCK:
        items = _STATE.get("items") or []
        for k in ks:
            if 0 <= k < len(items) and items[k]["state"] in ("retry", "paused", "going"):
                items[k].update(state="wait", again_at=0.0)
                items[k]["rev"] = _bump()


def _forms(n: int, one: str, few: str, many: str) -> str:
    d, h = n % 10, n % 100
    return one if d == 1 and h != 11 else few if 2 <= d <= 4 and not 12 <= h <= 14 else many


TRY_WORDS = {2: "со второй", 3: "с третьей", 4: "с четвёртой", 5: "с пятой"}


def _wait_paused(stop_check) -> bool:
    """While «Пауза»: True — «Продолжить» (or (v5.44.120) «Пропустить главу»: the paused one is left, the pause stays),
    False — «Остановить»."""
    started = time.monotonic()
    while _PAUSE.is_set() and not stop_check() and not _SKIP.is_set():
        time.sleep(0.2)
    with _LOCK:
        _STATE["paused_for"] = float(_STATE.get("paused_for") or 0.0) + (time.monotonic() - started)
        _SAMPLES.clear()                            # the speed of after the pause, not of before it
        _bump()
    return not stop_check()


def _sleep_until(when: float, halt) -> None:
    while not halt():
        left = when - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.25, left))


def _attempt(k: int, take, chapter, halt, say, shut: set) -> tuple:
    """The chapter asked once: of its own site, then (another site's copy) of the next.  ("done", the folder's name,
    the take that gave it) / ("halted", None, None) — «Остановить» or «Пауза» half-way / ("failed", why, whether
    asking again may give it)."""
    from core import manhwa_sources as S
    options = [take] + ([take.alt] if getattr(take, "alt", None) is not None else [])
    last, last_site, again = None, "", False
    for n, option in enumerate(options):
        if n in shut:
            continue
        if last is not None:
            say(f"{take.label}: с {last_site} не скачалась ({last}) — беру с {option.source.label}")
        item(k, **{"from": option.source.label if option.helped else "", "state": "going", "got": 0, "pages": 0})
        try:
            name = chapter(option, k, halt)
        except (S.Paid, S.Closed) as exc:          # asking again gives nothing (nothing is bought, no captcha solved)
            if halt():
                return "halted", None, None
            shut.add(n)
            last, last_site = exc, option.source.label
            continue
        except S.SourceError as exc:
            if halt():
                return "halted", None, None
            last, last_site, again = exc, option.source.label, True
            continue
        except OSError as exc:                     # a file not written (busy, the disk): it may be written next time
            if halt():
                return "halted", None, None
            last, last_site, again = S.SourceError(f"не записалась: {exc}"), option.source.label, True
            continue
        if not name:
            return "halted", None, None
        return "done", name, option
    return "failed", last, again


def go(takes: list, chapter, *, say, stop_check) -> dict:
    """Every chapter of ``takes`` by ``chapter(take, k, halt)`` (-> the chapter's folder name; "" when ``halt()``
    stopped it half-way) — in their order, with «Пауза» and the chapters that failed asked again.
    -> {"got": [(k, name, the take that gave it)], "failed": [{label, why, id}], "stopped", "gave_up": words}."""
    def halt() -> bool:
        return stop_check() or _PAUSE.is_set() or _SKIP.is_set()

    def skipped(k: int) -> None:
        """(v5.44.120) «Пропустить главу»: chapter ``k`` left — one that did not come, «пропущена»."""
        _SKIP.clear()
        pending.pop(k, None)
        failed.append((k, {"label": takes[k].label, "why": SKIPPED, "id": takes[k].id}))
        item(k, state="failed", why=SKIPPED, again_at=0.0)
        say(f"{takes[k].label}: пропущена — «Пропустить главу»; качаю следующую")

    pending = {k: 0.0 for k in range(len(takes))}   # a chapter → when it may be asked (again)
    tries: dict = {}
    shut: dict = {}
    got, failed, stopped, gave_up, in_a_row = [], [], False, "", 0
    while pending:
        if stop_check():
            stopped = True
            break
        if _SKIP.is_set():                          # (v5.44.120) asked while no chapter went (between two): the next
            with _LOCK:                             # one is not skipped by it — only the one paused, if any
                held = [x["k"] for x in _STATE.get("items") or [] if x.get("state") == "paused" and x["k"] in pending]
            if held:
                skipped(held[0])
                continue
            _SKIP.clear()
        if _PAUSE.is_set():
            if not _wait_paused(stop_check):
                stopped = True
                break
            continue
        now = time.monotonic()
        ready = [k for k, at in pending.items() if at <= now]
        if not ready:
            _sleep_until(min(pending.values()), halt)
            continue
        k = min(ready)                              # the earliest chapter: a retry whose wait is over comes first
        del pending[k]
        take = takes[k]
        outcome, value, extra = _attempt(k, take, chapter, halt, say, shut.setdefault(k, set()))
        if outcome == "done":
            in_a_row = 0
            got.append((k, value, extra))
            item(k, state="done", why="", again_at=0.0)
            say(f"{take.label}: готово" + (f" (с {extra.source.label})" if extra is not take and extra.helped else "")
                + (f" — {TRY_WORDS.get(tries[k] + 1, f'с {tries[k] + 1}-й')} попытки" if tries.get(k) else ""))
            continue
        if outcome == "halted":
            if stop_check():
                item(k, state="wait", again_at=0.0)
                stopped = True
                break
            if _SKIP.is_set():                      # (v5.44.120) «Пропустить главу»: left, the next one goes
                skipped(k)
                continue
            item(k, state="paused")                 # «Пауза»: the same chapter first after «Продолжить»
            pending[k] = 0.0
            continue
        n = tries.get(k, 0)
        if extra and n < len(RETRY_WAITS):
            wait = RETRY_WAITS[n]
            tries[k] = n + 1
            pending[k] = time.monotonic() + wait
            item(k, state="retry", why=str(value), tries=n + 1, again_at=pending[k])
            say(f"{take.label}: не скачалась — {value}; проверю ещё раз через {wait:g} с "
                f"(повтор {n + 1} из {len(RETRY_WAITS)})" + (", пока качаются следующие" if len(pending) > 1 else ""))
            continue
        failed.append((k, {"label": take.label, "why": str(value), "id": take.id}))
        item(k, state="failed", why=str(value), again_at=0.0)
        say(f"{take.label}: не скачалась — {value}" + (f" (и после {n} {_forms(n, 'повтора', 'повторов', 'повторов')})"
                                                     if n else ""))
        in_a_row += 1
        if in_a_row >= GIVE_UP_AFTER and pending:
            gave_up = (f"{in_a_row} {_forms(in_a_row, 'глава', 'главы', 'глав')} подряд не скачались, каждая — и "
                       f"после повторов: похоже, сайт сейчас их не отдаёт. Остальные ({len(pending)}) скачаются в "
                       f"следующий раз — ночью или по «Скачать»")
            say(gave_up)
            _settle(list(pending))
            break
    if stopped:
        _settle(list(pending))
    got.sort(key=lambda x: x[0])                    # in the order of reading, whichever came first
    return {"got": got, "failed": [x for _k, x in sorted(failed, key=lambda x: x[0])], "stopped": stopped,
            "gave_up": gave_up}
