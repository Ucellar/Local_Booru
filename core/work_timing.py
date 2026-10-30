"""Where a parser run's time goes: the steps of reading and writing its files — reading a file, waiting for the write
lock, copying the file into the library, the base, the preview, the stages after the sites — summed per step.  The
run says them in its journal every few minutes (``core/parser/runner.ParserRunner._pass``: «ВРЕМЯ …»), so a slow
run on the person's computer shows what is slow there (a disk, the base, a preview of big pictures) without guessing.
(Outside ``core/parser``: the import pipeline, shared by every writer, adds its own steps — the wait for the base and
the base itself — and must not import the parser package.)"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

# the steps, in the order they are said
READ = "чтение файла"
WAIT = "ожидание записи"
COPY = "копия в библиотеку"
BASE = "запись в базу"
IMPORT_WAIT = "ожидание базы"
PREVIEW = "превью"
VIDEO = "длина видео"
JOURNAL = "журнал сайтов"
AFTER = "после сайтов"
# what «после сайтов» is made of (v5.32): a file no site had by its MD5 — each check of it, the sites' answers; said
# in brackets after it (they overlap: the sites are asked at once)
AFTER_SUB = AFTER + " → "
AFTER_SOURCE_MD5 = AFTER_SUB + "MD5 поста-источника"
AFTER_NAME_MD5 = AFTER_SUB + "MD5 из имени файла"
AFTER_PIXIV = AFTER_SUB + "pixiv из имени файла"
AFTER_R34_KEY = AFTER_SUB + "ключ rule34 из имени"
AFTER_NAME_POST = AFTER_SUB + "пост из имени файла"
AFTER_GALLERY = AFTER_SUB + "та же картинка в галерее"
AFTER_PIXELS = AFTER_SUB + "пиксели"
AFTER_PIXEL_HASH = AFTER_SUB + "пиксели: хэш файла"      # (v5.34) the file's pixels read and hashed
AFTER_PIXEL_SITE = AFTER_SUB + "пиксели: ответ "         # + the site's name: the wait for its answer (its queue)
# (v5.35.1) + the site's name: its queue's requests — the site's turn and its answer; how it asks (in packs, one file a
# request and why) is said after it (``Steps.note``)
AFTER_PIXEL_ASK = AFTER_SUB + "пиксели: запрос к "
# (v5.35.2) + the site's name: a file not asked there — the site takes one file a request and its queue is long
AFTER_PIXEL_SKIP = AFTER_SUB + "пиксели: не ждали очередь к "
AFTER_RELAY = AFTER_SUB + "другие сайты по находке"
AFTER_THREAD = AFTER_SUB + "ожидание потока к сайтам"
AFTER_SITE = AFTER_SUB + "ответ "      # + the site's name: one step per site
# (v5.35.1) a reverse-search service's hits looked up on the sites: the posts behind them (by MD5 — on every site, by
# a post's address — on its site) and their previews compared; its parts — each site's answer
FINDS = "находки обратного поиска"
FINDS_SUB = FINDS + " → "
FINDS_POST = FINDS_SUB + "пост с "     # + the site's name: a post fetched by its address
PIXIV_SEARCH = "pixiv_id на "           # (v5.35.3) after a stage's «→ » + the site's name: a pixiv work's posts there
# (v5.34) a find of reverse search: its post's MD5 asked of the other sites (their tags join the card)
RELAY = "добор тегов к найденному"
RELAY_SUB = RELAY + " → "
PREVIEW_CHECK = "превью с сайта"       # a site's preview fetched and compared (after the sites and reverse search)
PREVIEW_HOST = PREVIEW_CHECK + " → "   # + the picture's host (v5.35)
PIXIV_ORIGINALS = "оригиналы pixiv"    # a pixiv work's originals fetched for their MD5
BRAK = "запись в Брак"
ORDER = (READ, WAIT, COPY, VIDEO, IMPORT_WAIT, BASE, PREVIEW, JOURNAL, AFTER, FINDS, RELAY, PREVIEW_CHECK,
         PIXIV_ORIGINALS, BRAK)
AFTER_ORDER = (AFTER_SOURCE_MD5, AFTER_NAME_MD5, AFTER_PIXIV, AFTER_R34_KEY, AFTER_NAME_POST, AFTER_GALLERY,
               AFTER_PIXELS, AFTER_PIXEL_HASH, AFTER_PIXEL_SITE, AFTER_PIXEL_ASK, AFTER_PIXEL_SKIP, AFTER_RELAY,
               AFTER_SUB + PIXIV_SEARCH, AFTER_SITE, AFTER_THREAD)
GROUPS = {AFTER: AFTER_ORDER,
          FINDS: (FINDS_SUB + "ответ ", FINDS_POST, FINDS_SUB + PIXIV_SEARCH, FINDS_SUB + "ожидание потока к сайтам"),
          RELAY: (RELAY_SUB + "ответ ", RELAY_SUB + "ожидание потока к сайтам"),
          PREVIEW_CHECK: (PREVIEW_HOST,)}


class _Stage(threading.local):
    prefix = ""


# The stage a thread is doing now — its sites' answers are timed as its parts: AFTER_SUB in a file's stages after the
# sites (core/parser/engine.after_sites), FINDS_SUB while a service's hits are looked up (engine.reverse_take), RELAY_SUB
# while reverse search's find is asked of the other sites
STAGE = _Stage()


class Steps:
    def __init__(self):
        self._lock = threading.Lock()
        self._sums: dict = {}               # step -> [times, seconds, longest]
        self._notes: dict = {}              # step -> how it is done now («пачками…», «по одному файлу: …»)

    @contextmanager
    def timed(self, step: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(step, time.perf_counter() - started)

    def add(self, step: str, seconds: float) -> None:
        with self._lock:
            entry = self._sums.setdefault(step, [0, 0.0, 0.0])
            entry[0] += 1
            entry[1] += seconds
            entry[2] = max(entry[2], seconds)

    def take(self) -> dict:
        """What was summed since the last time (and start over)."""
        with self._lock:
            sums, self._sums = self._sums, {}
        return sums

    def note(self, step: str, text: str) -> None:
        """How ``step`` is done now — said after it while it is done ("" — nothing to say)."""
        with self._lock:
            if text:
                self._notes[step] = text
            else:
                self._notes.pop(step, None)

    def notes(self) -> dict:
        with self._lock:
            return dict(self._notes)


STEPS = Steps()             # one parser run at a time (core/parser/jobs.py): one account (other writers add to it too)


def _seconds(value: float) -> str:
    if value < 0.095:
        return f"{value * 1000:.0f} мс"
    return f"{value:.1f}".replace(".", ",") + " с"


def _part(name: str, entry: list, note: str = "") -> str:
    times, total, longest = entry
    average = total / times
    part = f"{name} {times} × {_seconds(average)}"
    extra = [f"дольше всех {_seconds(longest)}"] if longest >= 5 * average and longest >= 1.0 else []
    extra += [note] if note else []
    return part + (f" ({'; '.join(extra)})" if extra else "")


def _inner(group: str, order: tuple, sums: dict, notes: dict) -> str:
    """The parts of ``group`` in brackets' words: the steps of ``order`` in it, a step that ends with a space — every
    site's one (sorted)."""
    sub = group + " → "
    parts = []
    for step in order:
        if step.endswith(" "):
            parts += [s for s in sorted(sums) if s.startswith(step)]
        elif step in sums:
            parts.append(step)
    return "; ".join(_part(step[len(sub):], sums[step], notes.get(step, "")) for step in parts)


def say(sums: dict, minutes: float, notes: dict | None = None) -> str:
    """«ВРЕМЯ за 5 мин: чтение файла 412 × 50 мс · …» — each step: how many times, on average, the longest when it
    stands out, how it is done now (``notes`` — ``Steps.notes``); what «после сайтов» and «добор тегов к найденному»
    are made of in brackets after them; "" — nothing was done."""
    sums = {step: entry for step, entry in sums.items() if entry and entry[0]}
    notes = notes or {}
    inner = {group: _inner(group, order, sums, notes) for group, order in GROUPS.items()}
    parts = []
    rest = sorted(step for step in sums if step not in ORDER and not any(step.startswith(g + " → ") for g in GROUPS))
    for step in ORDER + tuple(rest):
        if inner.get(step):
            head = _part(step, sums[step], notes.get(step, "")) if step in sums else f"{step} (ещё идут)"
            parts.append(f"{head} [{inner[step]}]")
        elif step in sums:
            parts.append(_part(step, sums[step], notes.get(step, "")))
    if not parts:
        return ""
    return f"ВРЕМЯ за {minutes:.0f} мин: " + " · ".join(parts)
