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

(v5.44.146) The owner (10.11.2026), of a title whose chapters ReadManga has taken down while com-x.life has them all:
«readmanga вроде находит но не качает … но почему оно не пытается найти на других а просто сразу в ошибку кидает».
A chapter is asked of **every** site that has it free (:func:`options_of` — the take and each next ``Take.alt``), not
of two; a site that has refused :data:`COLD_AFTER` chapters of this download in a row for good (taken down, paid,
closed) is asked after the others from then on; a chapter no site asked has given is not said failed before the other
sites have been asked once more (``go(rescue=)`` — ``manhwa_download.download`` asks the ones that did not answer
when the title was found); why a chapter did not come names every site asked (:func:`_why`); the words of giving up
tell «no site has them» from «the site gives none now».

(v5.44.163) The owner (12.11.2026): «Не скачалось ни одной главы из 16: 5 глав подряд не дал ни один сайт» — the five
had one site, the eleven after them five.  ``GIVE_UP_AFTER`` chapters in a row that did not come stop only the
chapters left that have no site but the ones those were asked of (:func:`_site_names`): they wait for the next
time, the others go on (the sites that gave nothing asked last — ``cold``).  And the processor — «проц долбится в
100%» while the round of new chapters went by itself: that round (``go(hold=)``, ``manhwa_download.watch_round``)
waits while the person is at the computer (:func:`wait_away` — before the next chapter, before the next title; a
chapter going is finished); «Продолжить» of «Загрузки» (:func:`go_on`) lets this round go on with the person there.
"""
from __future__ import annotations

import threading
import time
from collections import deque

RETRY_WAITS = (5.0, 15.0, 30.0)     # a chapter that did not come is asked again after these (the owner: «3 раза»)
GIVE_UP_AFTER = 5                   # chapters in a row that did not come, each asked again: the site gives none now
SPEED_SPAN = 8.0                    # seconds the speed is counted over
SKIPPED = "пропущена"               # (v5.44.120) why a chapter skipped by «Пропустить главу» did not come
COLD_AFTER = 3                      # (v5.44.146) a site's refusals for good in a row (taken down, paid, closed) after
#                                     which it is asked after the other sites till this download ends
SEEKING = "ищу на других сайтах"     # (v5.44.146) a chapter no site asked gave, while the other sites are asked

# (v5.44.163) the round of new chapters while it waits for the person to leave the computer: its row of «Загрузки»
# (the button is beside it) and its journal
HELD_ROW = ("Жду, пока вы отойдёте от компьютера, — чтобы не занимать процессор, пока он вам нужен. "
            "Нужно сейчас — «Продолжить»")
HELD_WORDS = HELD_ROW + " в «Загрузках»"
HELD_SAY_EVERY = 600.0              # …and of its journal: not more often than this (the person comes and goes)

_PAUSE = threading.Event()          # «Пауза» of the download going (one at a time)
_SKIP = threading.Event()           # (v5.44.120) «Пропустить главу»: the chapter going (or paused) is left for now
_HOLD = threading.Event()           # (v5.44.163) the round of new chapters waits for the person to leave the computer
_GO_ON = threading.Event()          # …«Продолжить» said of it: this round goes on with the person here
_HELD_SAID: dict = {"at": None}     # when (``time.monotonic``) this round's journal was last told so
_YIELD: dict = {"to": None}         # …a download of the person's waits (a function): the round that waits gives way
_WAY = threading.Event()            # …and has given way: it ends now, to be begun again later
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


def held() -> bool:
    """(v5.44.163) The round of new chapters waits now for the person to leave the computer (:func:`wait_away`)."""
    return _HOLD.is_set()


def go_on() -> None:
    """(v5.44.163) «Продолжить» of a round that waits for the person to leave (or «Проверить сейчас»): it goes on
    now and does not wait again until it ends (:func:`let_go`)."""
    _GO_ON.set()


def let_go() -> None:
    """(v5.44.163) The round has ended (or begins): the next one waits for the person to leave again."""
    _GO_ON.clear()
    _HOLD.clear()
    _WAY.clear()
    _YIELD["to"] = None
    _HELD_SAID["at"] = None


def yield_to(waiting) -> None:
    """(v5.44.163) ``waiting()`` — a download of the person's waits in the queue for this round to end: while the
    round waits for him to leave it would wait for ever — the round gives way (:func:`gave_way`) and ends."""
    _YIELD["to"] = waiting


def gave_way() -> bool:
    """(v5.44.163) The round that waited for the person to leave has given way to his download (:func:`yield_to`)."""
    return _WAY.is_set()


def wait_away(here, say, stop_check) -> bool:
    """(v5.44.163) The round of new chapters before its next title and its next chapter: while ``here()`` — the
    person is at the computer (a key, the mouse, a page of the program opened; a video playing in the window) — it
    waits (the owner: «проц долбится в 100%» — the round opened sites in the window's browser while he was at
    it), unless «Продолжить» was said of it (:func:`go_on`).  ``here`` None — a download nobody holds (the
    person's own).  The journal is told so once in :data:`HELD_SAY_EVERY` seconds; the page shows it by
    :func:`held`.  A download of the person's put in the queue meanwhile (:func:`yield_to`) — the round gives way.
    -> False — stopped meanwhile, or given way (:func:`gave_way`)."""
    if _WAY.is_set():
        return False
    if here is None or _GO_ON.is_set() or not here():
        return not stop_check()
    started = time.monotonic()
    if _HELD_SAID["at"] is None or started - _HELD_SAID["at"] >= HELD_SAY_EVERY:
        _HELD_SAID["at"] = started
        say(HELD_WORDS)
    with _LOCK:
        _HOLD.set()
        _bump()
    try:
        while not stop_check() and not _GO_ON.is_set() and here():
            waiting = _YIELD["to"]
            if waiting is not None and waiting():   # the person asked for a download: it would wait for him to
                _WAY.set()                          # leave — the round ends instead, and begins again later
                break
            time.sleep(0.25)
    finally:
        with _LOCK:
            _HOLD.clear()
            _STATE["paused_for"] = float(_STATE.get("paused_for") or 0.0) + (time.monotonic() - started)
            _SAMPLES.clear()                        # the speed of after the wait, not of before it
            _bump()
    return not stop_check() and not _WAY.is_set()


def _bump() -> int:
    _COUNT["rev"] += 1
    _STATE["rev"] = _COUNT["rev"]
    return _COUNT["rev"]


def options_of(take) -> list:
    """(v5.44.146) A chapter's take and its other sites' copies — each one's ``alt`` — in their order."""
    out: list = []
    while take is not None and all(take is not seen for seen in out):
        out.append(take)
        take = getattr(take, "alt", None)
    return out


def _item_of(k: int, take, rev: int) -> dict:
    return {"k": k, "id": take.id, "label": take.label, "name": take.name,
            "from": take.source.label if take.helped and take.source is not None else "", "state": "wait",
            "pages": 0, "got": 0, "why": "", "tries": 0, "again_at": 0.0, "rev": rev}


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
                      items=[_item_of(k, t, rev) for k, t in enumerate(takes)])
        _SAMPLES.clear()


def more(takes: list) -> None:
    """(v5.44.146) Chapters added to the download going — another site has them free after all: waiting, after the
    ones it began with."""
    with _LOCK:
        items = _STATE.setdefault("items", [])
        rev = _bump()
        for take in takes:
            items.append(_item_of(len(items), take, rev))
        _STATE["total"] = len(items)


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
    chapters came), paused, held ((v5.44.163) the round waits for the person to leave: :func:`wait_away`), finished,
    gave_up."""
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
                 speed=speed, paused=_PAUSE.is_set(), held=_HOLD.is_set(),
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


def _why(whys: dict) -> str:
    """(v5.44.146) Why a chapter did not come, of what each site asked said (its label → its words): one site — its
    words as they are; several — each one's, named unless its words name it."""
    if not whys:
        return "ни один сайт её не дал"
    if len(whys) == 1:
        return next(iter(whys.values()))
    return "; ".join(words if not site or site.casefold() in words.casefold() else f"{site}: {words}"
                     for site, words in whys.items())


def _site_names(take) -> list:
    """[(a site's name, its label)] of the sites a chapter is asked of (:func:`options_of`), in their order."""
    out = []
    for n, option in enumerate(options_of(take)):
        label = str(getattr(option.source, "label", "") or "")
        out.append((str(getattr(option.source, "name", "") or label or n), label))
    return out


def _attempt(k: int, take, chapter, halt, say, shut: set, cold: dict, whys: dict) -> tuple:
    """The chapter asked once: of its own site, then — another site's copy — of each next one (:func:`options_of`;
    (v5.44.146) every site that has it, not two).  ("done", the folder's name, the take that gave it) / ("halted",
    None, None) — «Остановить» or «Пауза» half-way / ("failed", why — the words of every site asked (``whys``, kept
    from one asking of the chapter to the next), whether asking again may give it).  ``shut`` — the sites that refused
    this chapter for good (not asked of it again); ``cold`` — each site's refusals for good in a row in this download."""
    from core import manhwa_sources as S
    options = options_of(take)
    names, labels = [x[0] for x in _site_names(take)], [x[1] for x in _site_names(take)]
    # (v5.44.136) a site that gives the program no pictures lately, (v5.44.146) or one that has refused ``COLD_AFTER``
    # chapters of this download in a row for good — after the others (the order of the rest stays as it is)
    order = sorted(range(len(options)), key=lambda n: bool(labels[n] and S.withheld(labels[n]))
                   or cold.get(names[n], 0) >= COLD_AFTER)
    last, last_site, again = None, "", False
    for n in order:
        option, site, label = options[n], names[n], labels[n]
        if site in shut:
            continue
        if last is not None:
            say(f"{take.label}: с {last_site} не скачалась ({last}) — беру с {label}")
        item(k, **{"from": label if option.helped else "", "state": "going", "got": 0, "pages": 0})
        try:
            name = chapter(option, k, halt)
        except (S.Paid, S.Closed, S.Withheld) as exc:   # asking again gives nothing (nothing is bought, no captcha
            #                                             solved, (v5.44.136) no site's protection got around)
            if halt():
                return "halted", None, None
            shut.add(site)
            cold[site] = cold.get(site, 0) + 1
            last, last_site = exc, label
            whys[label] = str(exc)
            continue
        except S.SourceError as exc:
            if halt():
                return "halted", None, None
            last, last_site, again = exc, label, True
            whys[label] = str(exc)
            continue
        except OSError as exc:                     # a file not written (busy, the disk): it may be written next time
            if halt():
                return "halted", None, None
            last, last_site, again = S.SourceError(f"не записалась: {exc}"), label, True
            whys[label] = str(last)
            continue
        if not name:
            return "halted", None, None
        cold[site] = 0
        return "done", name, option
    return "failed", _why(whys), again


def go(takes: list, chapter, *, say, stop_check, rescue=None, hold=None) -> dict:
    """Every chapter of ``takes`` by ``chapter(take, k, halt)`` (-> the chapter's folder name; "" when ``halt()``
    stopped it half-way) — in their order, with «Пауза» and the chapters that failed asked again.
    (v5.44.146) ``rescue(k, left, why)`` — the other sites asked once more (``manhwa_download.download``): called
    when chapter ``k`` did not come for good (``why`` — the words of the sites asked), before it is said failed, and
    with None once every chapter has been asked (the ones that waited for a free site); ``left`` — the places still
    to come; -> None, or ({place: its take with more sites}, [takes to add after the last]).
    (v5.44.163) ``hold()`` — the person is at the computer (the round of new chapters alone gives it): the next
    chapter waits for him to leave (:func:`wait_away`).  ``GIVE_UP_AFTER`` chapters in a row that did not come put off
    the chapters left that have no other site than theirs; the ones that have go on.
    -> {"got": [(k, name, the take that gave it)], "failed": [{label, why, id}], "stopped", "gave_up": words}."""
    takes = list(takes)                             # (v5.44.146) the rescue swaps takes and adds some: this run's own

    def halt() -> bool:
        return stop_check() or _PAUSE.is_set() or _SKIP.is_set()

    def skipped(k: int) -> None:
        """(v5.44.120) «Пропустить главу»: chapter ``k`` left — one that did not come, «пропущена»."""
        _SKIP.clear()
        pending.pop(k, None)
        failed.append((k, {"label": takes[k].label, "why": SKIPPED, "id": takes[k].id}))
        item(k, state="failed", why=SKIPPED, again_at=0.0)
        say(f"{takes[k].label}: пропущена — «Пропустить главу»; качаю следующую")

    def rescued(k, why: str = "") -> bool:
        """(v5.44.146) What ``rescue`` brought, taken in: the takes with more sites in their places, the chapters
        another site has free after all — after the last.  -> chapter ``k`` has a site more to ask."""
        if rescue is None:
            return False
        brought = rescue(k, sorted(pending), why)
        if not brought:
            return False
        swaps, added = brought
        for place, fresh in swaps.items():
            if place == k or place in pending:
                takes[place] = fresh
        if added:
            first = len(takes)
            takes.extend(added)
            more(added)
            pending.update({first + n: 0.0 for n in range(len(added))})
        return k is not None and k in swaps

    pending = {k: 0.0 for k in range(len(takes))}   # a chapter → when it may be asked (again)
    tries: dict = {}
    shut: dict = {}
    whys: dict = {}
    cold: dict = {}
    got, failed, stopped, gave_up, in_a_row, for_good, end_asked = [], [], False, "", 0, 0, False
    streak: dict = {}                               # (v5.44.163) the sites of the chapters that did not come in a row
    put_off = 0                                     # …and the chapters put off with them so far (no other site)
    while True:
        if not pending:
            if end_asked or rescue is None or stop_check():
                break
            end_asked = True                        # (v5.44.146) every chapter asked: the ones that waited for a free
            rescued(None)                           # site — the other sites asked once more for them
            continue
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
        if hold is not None and not _GO_ON.is_set() and hold():     # (v5.44.163) the round's own download: the
            if not wait_away(hold, say, stop_check):                # next chapter — when the person has left
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
        outcome, value, extra = _attempt(k, take, chapter, halt, say, shut.setdefault(k, set()), cold,
                                         whys.setdefault(k, {}))
        if outcome == "done":
            in_a_row = for_good = 0
            streak.clear()
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
        if rescued(k, str(value)):                  # (v5.44.146) another site has it after all: asked there at once
            tries.pop(k, None)
            pending[k] = 0.0
            item(k, state="wait", why="", tries=0, again_at=0.0, seek=False)
            continue
        if stop_check():                            # «Остановить» while the other sites were asked
            item(k, state="wait", again_at=0.0, seek=False)
            stopped = True
            break
        failed.append((k, {"label": take.label, "why": str(value), "id": take.id}))
        item(k, state="failed", why=str(value), again_at=0.0, seek=False)
        say(f"{take.label}: не скачалась — {value}" + (f" (и после {n} {_forms(n, 'повтора', 'повторов', 'повторов')})"
                                                     if n else ""))
        in_a_row += 1
        for_good += 0 if extra else 1
        streak.update(_site_names(take))
        if in_a_row >= GIVE_UP_AFTER and pending:
            # (v5.44.163) the chapters left that have a site none of those in a row was asked of go on: until now
            # five chapters one site alone had ended the download of eleven more that five sites had
            hopeless = [p for p in sorted(pending) if {name for name, _label in _site_names(takes[p])} <= set(streak)]
            if len(hopeless) < len(pending):
                where = ", ".join(str(label or name) for name, label in streak.items())
                told = (f"{in_a_row} {_forms(in_a_row, 'главу', 'главы', 'глав')} подряд не дал ни один из их "
                        f"сайтов ({where})")
                going = len(pending) - len(hopeless)
                if hopeless:
                    for place in hopeless:
                        del pending[place]
                    _settle(hopeless)
                    put_off += len(hopeless)
                    gave_up = (f"{told}. Только там есть ещё {put_off} {_forms(put_off, 'глава', 'главы', 'глав')} "
                               f"— отложено до следующего раза (ночью или по «Скачать»)")
                    say(f"{gave_up}; остальные ({going}) есть на других сайтах — качаю их")
                else:
                    say(f"{told}; у остальных ({going}) есть и другие сайты — качаю дальше")
                in_a_row = for_good = 0
                streak.clear()
                continue
            left = len(pending) + put_off
            if for_good:                            # (v5.44.146) no site has them — not «the site gives none now»
                gave_up = (f"{in_a_row} {_forms(in_a_row, 'главу', 'главы', 'глав')} подряд не дал ни один сайт — "
                           f"похоже, и остальных сейчас нигде нет. Остальные ({left}) попробую в следующий "
                           f"раз — ночью или по «Скачать»")
            else:
                gave_up = (f"{in_a_row} {_forms(in_a_row, 'глава', 'главы', 'глав')} подряд не скачались, каждая — и "
                           f"после повторов: похоже, сайт сейчас их не отдаёт. Остальные ({left}) попробую в "
                           f"следующий раз — ночью или по «Скачать»")
            say(gave_up)
            _settle(list(pending))
            break
    if stopped:
        _settle(list(pending))
    got.sort(key=lambda x: x[0])                    # in the order of reading, whichever came first
    return {"got": got, "failed": [x for _k, x in sorted(failed, key=lambda x: x[0])], "stopped": stopped,
            "gave_up": gave_up}
