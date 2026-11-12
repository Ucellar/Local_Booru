"""One parser run at a time for the whole program, and its state for the pages that show it.

Whoever starts the parser — the web page «Парсер», a «Брак» retry, «Диагностика» — goes
through :func:`claim`: a second run is refused while one is going (two runs over the same files
and the same sites would fight for the database and the site limits).  :class:`ParserJob` runs
:class:`ParserRunner` in a thread and keeps what a page polls: status, progress, counters, the last
lines of the log (numbered, so a page asks only for lines it has not seen), what every site or
service is doing, the files in work and the last answers.  A file the page shows has a number in
its run (:meth:`ParserJob.path_of`): the page asks for its picture by that number, never by a path.
:func:`is_running` replaces the old ``settings["_parser_running"]`` flag for code that must yield to
the parser (maintenance, heavy gallery queries).

(v5.44.160; the page «Парсер» anew — the mockup A) A file in work also says what each site and service has answered
about it so far (``files[n]["sites"]`` — the card of the file on the page), and the end of a run is noted on the disk
(:func:`last_run`): the page of a program started anew still says when the parser ran last and what it found.
"""
from __future__ import annotations

import heapq
import json
import re
import threading
import time
import traceback
from collections import deque
from pathlib import Path

LOG_KEEP = 5000                 # lines a page can still fetch
RECENT_KEEP = 48                # answers of files a page shows («Готово недавно»)
FILES_KEEP = 400                # files of a run whose pictures a page may still ask for (the newest)
FLIGHT_SHOWN = 24               # files in work a page shows as pictures (the longest in work); the rest — a number
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
COUNT_KEYS = ("tagged", "nomatch", "deferred", "skip", "error")
LAST_RUN_FILE = "parser_last_run.json"      # (v5.44.160) the end of the last run, in the folder of the run's notes
_CLAIM_LOCK = threading.Lock()
_OWNER: str = ""                # who runs the parser now ("" — nobody)
_CURRENT: "ParserJob | None" = None
_LAST: "ParserJob | None" = None
_SEQ = 0
_EXIT_HOOKED = False

_SITE_LINE = re.compile(r"^\[MD5:([^:\]]+):[^\]]*\]")
_FILE_LINE = re.compile(r"^\[(?:MD5|REVERSE):[^\]]*\]")
# (v5.44.125) «Манга через парсер» (core/parser_manga.line): «МАНГА: …», «НЕ МАНГА: …», «ЖДЁТ: …», «СБОРКА: …»,
# «МАНГА В ПАПКЕ: …» — the tab «Манга» of the journal
_MANGA_LINE = re.compile(r"^(?:МАНГА(?: В ПАПКЕ)?|НЕ МАНГА|ЖДЁТ|СБОРКА): ")


class ParserBusy(RuntimeError):
    """The parser is already running (``owner`` says who started it)."""

    def __init__(self, owner: str):
        super().__init__(busy_text(owner))
        self.owner = owner


# «Диагностика → Обслуживание» takes the parser (and the subscriptions) for work that rewrites much of the
# library (VACUUM, the index…), so that nothing writes to it meanwhile: its owner names begin so.
MAINTENANCE_OWNER = "обслуживание «{}»"


def busy_text(owner: str) -> str:
    """Why a parser run cannot start now, in words."""
    if owner.startswith(MAINTENANCE_OWNER.split("«")[0]):
        return f"Сейчас идёт {owner} (Настройки → Проверка и починка → Обслуживание)"
    return f"Парсер уже работает ({owner})"


def claim(owner: str) -> bool:
    """Take the parser for ``owner``; False when somebody else has it."""
    global _OWNER
    with _CLAIM_LOCK:
        if _OWNER and _OWNER != owner:
            return False
        _OWNER = owner
        return True


def release(owner: str) -> None:
    global _OWNER
    with _CLAIM_LOCK:
        if _OWNER == owner:
            _OWNER = ""


def owner() -> str:
    with _CLAIM_LOCK:
        return _OWNER


def is_running() -> bool:
    return bool(owner())


def current() -> "ParserJob | None":
    """The run in progress, else the last finished one (its result stays on the page)."""
    with _CLAIM_LOCK:
        return _CURRENT or _LAST


def channel_of(line: str) -> str:
    """The log channel of a parser line (the protocol of core/parser/engine.py): a site, «final»
    (the result of a file), «reverse» (reverse search), (v5.44.125) «manga» (core/parser_manga.py) or «main»."""
    text = str(line or "")
    m = _SITE_LINE.match(text)
    if m:
        return "site:" + m.group(1).strip()
    if text.startswith("[REVERSE:"):        # (v5.44.35) also «[REVERSE:…] … — так же ещё N файлов» (core/parser/tally.py)
        return "final" if "ФИНАЛ" in text else "reverse"
    if _MANGA_LINE.match(text):
        return "manga"
    return "main"


class ParserJob:
    """A parser run started from a page; :meth:`snapshot` is what the page shows."""

    def __init__(self, settings: dict, *, label: str = "Парсер", kind: str = "run"):
        global _SEQ
        with _CLAIM_LOCK:
            _SEQ += 1
            self.id = _SEQ
        self.settings = dict(settings)
        self.label = label
        self.kind = kind
        self.owner = f"web:{self.id}"
        self.status = "starting"          # starting → running ⇄ paused → stopping → done / failed
        self.started_at = time.time()
        self.finished_at = 0.0
        self.done = 0
        self.total = 0
        self.current_file = ""
        self.summary: dict = {}
        self.error = ""
        self.activity: dict[str, dict] = {}
        self._files: dict[int, str] = {}       # number in this run → path (the newest FILES_KEEP)
        self._last_number = 0
        self._numbers: dict[str, int] = {}     # path → number
        self._flight: dict[int, float] = {}    # files in work → when taken
        self._answers: dict[int, dict] = {}    # (v5.44.160) a file in work → {a site or service: its last word of it}
        self._recent: deque = deque(maxlen=RECENT_KEEP)
        self._log: deque = deque(maxlen=LOG_KEEP)
        self._seq = 0
        self._lock = threading.Lock()
        self._runner = None
        self._thread: threading.Thread | None = None

    # ── control ─────────────────────────────────────────────────────────────
    def start(self) -> "ParserJob":
        global _CURRENT
        if not claim(self.owner):
            raise ParserBusy(owner())
        from core.parser.runner import ParserRunner
        try:
            self._runner = ParserRunner(self.settings, log=self._on_log, progress=self._on_progress,
                                        current_file=self._on_file, activity=self._on_activity,
                                        file_result=self._on_result)
        except BaseException:
            release(self.owner)
            raise
        with _CLAIM_LOCK:
            _CURRENT = self
        self.status = "running"
        self._thread = threading.Thread(target=self._run, name=f"parser-job-{self.id}", daemon=True)
        self._thread.start()
        _hook_exit()
        return self

    def _stop_for_exit(self) -> None:
        """Closing the program: files in progress are finished and written, new ones are not taken."""
        if self._thread is not None and self._thread.is_alive():
            self.stop()
            self._thread.join(20)

    def pause(self) -> None:
        if self._runner is not None and self.status == "running":
            self._runner.set_paused(True)
            self.status = "paused"
            self._on_log("ПАУЗА: новые запросы не отправляются, текущие доделываются")

    def resume(self) -> None:
        if self._runner is not None and self.status == "paused":
            self._runner.set_paused(False)
            self.status = "running"
            self._on_log("ПРОДОЛЖЕНИЕ")

    def stop(self) -> None:
        if self._runner is not None and self.status in {"running", "paused", "starting"}:
            self._runner.stop()
            self.status = "stopping"
            self._on_log("СТОП: доделываю начатые файлы, новые не беру")

    def site_again(self, host: str) -> str:
        """«Проверить снова» for a site switched off in this run (the runner asks it in the background): "" — started
        or already going; else why not."""
        if self._runner is None or self.status not in {"running", "paused"}:
            return "Парсер сейчас не работает"
        return self._runner.site_again(host)

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    # ── the run ─────────────────────────────────────────────────────────────
    def _run(self) -> None:
        global _CURRENT, _LAST
        try:
            self.summary = self._runner.run() or {}
            self.status = "done"
        except BaseException as exc:          # shown on the page, never a silent death
            self.error = f"{type(exc).__name__}: {exc}"
            self.status = "failed"
            self._on_log(f"ПАРСЕР ОСТАНОВЛЕН ОШИБКОЙ: {self.error}")
            self._on_log(traceback.format_exc()[-3000:])
        finally:
            self.finished_at = time.time()
            self.current_file = ""
            with self._lock:
                self._flight.clear()
                self._answers.clear()
            with _CLAIM_LOCK:
                if _CURRENT is self:
                    _CURRENT = None
                _LAST = self
            release(self.owner)
            self._note_end()
            try:
                from core.light_backup import checkpoint_sqlite
                checkpoint_sqlite(self.settings, truncate=True, optimize=True)
            except Exception as exc:          # a checkpoint is housekeeping: the run itself is over
                self._on_log(f"  сжатие журнала базы после запуска не удалось: {type(exc).__name__}: {exc}")

    def _on_log(self, message: str) -> None:
        from core.redaction import sanitize_text
        text = sanitize_text(message)          # API keys in an error's URL never reach a page
        with self._lock:
            for line in text.splitlines() or [""]:
                self._seq += 1
                self._log.append((self._seq, time.time(), channel_of(line), line))

    def _on_progress(self, done: int, total: int) -> None:
        self.done, self.total = int(done), int(total)

    def _on_file(self, path: str) -> None:
        self.current_file = str(path)
        with self._lock:
            number = self._number(str(path))
            self._flight.setdefault(number, time.time())

    def _on_activity(self, label: str, status: str, path: str) -> None:
        with self._lock:
            number = self._number(str(path)) if path else 0
            self.activity[str(label)] = {"status": str(status), "file": Path(str(path)).name if path else "",
                                         "file_id": number, "at": time.time()}
            if number in self._flight:        # (v5.44.160) the file's card: what each has said of it so far
                self._answers.setdefault(number, {})[str(label)] = str(status)

    def _counts(self) -> dict:
        counts = dict(getattr(self._runner, "counts", {}) or {}) if self._runner is not None else {}
        if self.summary:
            counts.update({k: v for k, v in self.summary.items() if isinstance(v, int)})
        return {k: int(counts.get(k, 0) or 0) for k in COUNT_KEYS}

    def _note_end(self) -> None:
        """(v5.44.160) The end of the run on the disk (:func:`last_run`): when, how long, what it found — the page of
        a program started anew says it.  The folder's name only, never its path.  A run over the folder only: «Искать
        снова» for a few cards of Брак does not take the place of the folder's last run."""
        if self.kind != "run":
            return
        try:
            from core.file_safety import atomic_write_bytes
            from core.paths import RUNTIME_DIR
            root = str(self.settings.get("root") or "")
            note = {"status": self.status, "kind": self.kind, "label": self.label, "started": int(self.started_at),
                    "finished": int(self.finished_at), "done": int(self.done), "total": int(self.total),
                    "counts": self._counts(), "folder": Path(root).name if root else "",
                    "stopped": bool((self.summary or {}).get("stopped"))}
            atomic_write_bytes(Path(RUNTIME_DIR) / LAST_RUN_FILE,
                               json.dumps(note, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:              # a note for the page: the run itself is over
            self._on_log(f"  заметка о конце запуска не записалась: {type(exc).__name__}: {exc}")

    def _on_result(self, path: str, result) -> None:
        """A file's answer: it leaves «в работе» and goes first in «Готово недавно» (once: a deferred file
        answered again later keeps only its newest answer)."""
        with self._lock:
            number = self._number(str(path))
            self._flight.pop(number, None)
            self._answers.pop(number, None)
            entry = {"id": number, "name": Path(str(path)).name, "kind": _kind(str(path)),
                     "status": str(getattr(result, "status", "") or ""), "reason": str(getattr(result, "reason", "") or ""),
                     "tags": int(getattr(result, "tag_count", 0) or 0), "md5": str(getattr(result, "md5", "") or ""),
                     "at": time.time()}
            for old in [e for e in self._recent if e["id"] == number]:
                self._recent.remove(old)
            self._recent.appendleft(entry)

    def _number(self, path: str) -> int:
        """The number of ``path`` in this run (under ``self._lock``); the oldest files no page shows any more
        are forgotten."""
        number = self._numbers.get(path)
        if number is None:
            self._last_number += 1
            number = self._last_number
            self._numbers[path] = number
            self._files[number] = path
            if len(self._files) > FILES_KEEP:
                shown = set(self._flight) | {e["id"] for e in self._recent} | {
                    int(a.get("file_id") or 0) for a in self.activity.values()}
                for old in sorted(self._files):
                    if len(self._files) <= FILES_KEEP:
                        break
                    if old in shown:
                        continue
                    self._numbers.pop(self._files.pop(old), None)
        return number

    def path_of(self, number: int) -> Path | None:
        """The file a page asks for by its number in this run (None — not a file of this run, or forgotten)."""
        with self._lock:
            path = self._files.get(int(number))
        return Path(path) if path else None

    # ── what a page shows ───────────────────────────────────────────────────
    def snapshot(self, after: int = 0, limit: int = 1000) -> dict:
        with self._lock:
            lines = [line for line in self._log if line[0] > after][-max(1, limit):]
            activity = {k: dict(v) for k, v in self.activity.items()}
            last_seq = self._seq
            # with the sites' queues thousands of files may wait for a slow site: a page shows the first of them
            waiting = [(n, at) for n, at in self._flight.items() if n in self._files]
            first = heapq.nsmallest(FLIGHT_SHOWN, waiting, key=lambda item: item[1])
            flight = [{"id": n, "name": Path(self._files[n]).name, "kind": _kind(self._files[n]), "since": int(at),
                       "sites": [[label, word] for label, word in (self._answers.get(n) or {}).items()],
                       "stage": "read", "path": self._files[n]} for n, at in first]
            flight_more = max(0, len(waiting) - len(flight))
            recent = [dict(e) for e in self._recent]
        self._where(flight)
        counts = self._counts()
        queues = self._queues()
        now = self.finished_at or time.time()
        elapsed = max(0.0, now - self.started_at)
        eta, eta_by = None, ""
        if self.status in {"running", "paused"} and self.total > self.done:
            eta, eta_by = self._estimate()
        return {
            "id": self.id, "label": self.label, "kind": self.kind, "status": self.status,
            "done": self.done, "total": self.total, "elapsed": int(elapsed), "eta": eta, "eta_by": eta_by,
            "current_file": Path(self.current_file).name if self.current_file else "",
            "counts": counts, "started": int(self.started_at), "finished": int(self.finished_at),
            "folder": Path(str(self.settings.get("root") or "")).name,
            "summary": self.summary, "error": self.error, "activity": activity, "files": flight, "recent": recent,
            "files_more": flight_more, "queues": queues,
            "log": [{"seq": seq, "t": int(ts), "channel": ch, "text": text} for seq, ts, ch, text in lines],
            "last_seq": last_seq,
        }


    def _where(self, flight: list) -> None:
        """(v5.44.160) Where each file in work is (``ParserRunner.waits``): its ``stage`` — «read» (being read),
        «sites» (some site is still to answer), «write» (found: being written), «after» (no site had it: the checks
        after the sites), «reverse» (reverse search) — and, after what the sites have said of it, the sites it still
        waits for: a site not asked yet — «очередь».  The path of the file is taken out: a page never gets it."""
        waits = getattr(self._runner, "waits", None)
        for item in flight:
            path = item.pop("path", "")
            if not callable(waits):
                continue
            try:
                where = waits(path)
            except Exception:  # the page's view of the run, not the run
                where = None
            if not where:
                continue
            item["stage"] = str(where.get("stage") or "sites")
            said = {label for label, _word in item["sites"]}
            item["sites"] += [[str(label), "очередь"] for label in where.get("waiting") or () if label not in said]

    def _estimate(self) -> tuple:
        """How long the run still takes and the queue that decides it — by the queues' own pace (``ParserRunner.
        estimate``); while it cannot be told yet — (None, the stage that has work and no pace yet: the page names it).
        Not the files done so far against the time gone: the files that need reverse search come first, and the rest
        («добор» in batches) go much faster."""
        estimate = getattr(self._runner, "estimate", None)
        if not callable(estimate):
            return None, ""
        try:
            seconds, slowest = estimate()
        except Exception:  # the page's view of the run, not the run
            return None, ""
        return (int(seconds), str(slowest or "")) if seconds is not None else (None, str(slowest or ""))

    def _queues(self) -> list:
        """The queues of the run (``ParserRunner.lanes``): the files' own (kind «files»: ``stage`` read — how many
        wait to be read, read, per minute, being read now; writes, after — tasks waiting, done, per minute, ``average``
        seconds a task), the sites' and the reverse-search services' — how each asks, how many files wait for it, how
        many it answered and how fast, which file it asks now (its name and number in the run — never the path)."""
        lanes = getattr(self._runner, "lanes", None)
        if not callable(lanes):
            return []
        try:
            rows = lanes()
        except Exception:  # the page's view of the run, not the run
            return []
        from core.redaction import sanitize_text
        out = []
        for row in rows:
            asking = str(row.get("asking") or "")
            with self._lock:
                number = self._numbers.get(asking, 0) if asking else 0
            out.append({"site": str(row.get("site") or ""), "host": str(row.get("host") or ""),
                        "name": str(row.get("name") or row.get("site") or ""),     # as ``eta_by`` names the slowest
                        "kind": str(row.get("kind") or "site"), "how": str(row.get("how") or ""),
                        "waiting": int(row.get("waiting") or 0), "answered": int(row.get("answered") or 0),
                        "per_minute": float(row.get("per_minute") or 0.0),
                        "eta": int(row["eta"]) if row.get("eta") is not None else None,
                        # (v5.44.160) with the files not read yet: 0 — nothing to do or it keeps up, None — not known
                        "left": int(row["left"]) if row.get("left") is not None else None,
                        "file": Path(asking).name if asking else "", "file_id": number,
                        "reading": int(row.get("reading") or 0), "stage": str(row.get("stage") or ""),
                        "average": float(row.get("average") or 0.0),
                        # a switched-off site: why (what the person can fix), and what «Проверить снова» said
                        "reason": sanitize_text(str(row.get("reason") or "")),
                        "again": sanitize_text(str(row.get("again") or ""))})
        return out


def last_run() -> dict | None:
    """(v5.44.160) The end of the last run as it was noted (``ParserJob._note_end``): its status, when it began and
    ended, how many files, the counts, the folder's name, whether it was stopped by hand — or None (no run yet, or
    the note does not read)."""
    try:
        from core.paths import RUNTIME_DIR
        note = json.loads((Path(RUNTIME_DIR) / LAST_RUN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict) or not isinstance(note.get("counts"), dict):
        return None
    try:
        return {"status": str(note.get("status") or ""), "kind": str(note.get("kind") or "run"),
                "label": str(note.get("label") or "")[:200], "started": int(note.get("started") or 0),
                "finished": int(note.get("finished") or 0), "done": int(note.get("done") or 0),
                "total": int(note.get("total") or 0), "folder": str(note.get("folder") or "")[:200],
                "stopped": bool(note.get("stopped")),
                "counts": {k: int(note["counts"].get(k) or 0) for k in COUNT_KEYS}}
    except (TypeError, ValueError):
        return None


def _kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    return "video" if ext in VIDEO_EXTS else ("gif" if ext == ".gif" else "image")


def _hook_exit() -> None:
    """Closing the program stops the run in progress (one hook for all runs: a hook per run kept
    every finished run, with its log, until the program closed)."""
    global _EXIT_HOOKED
    with _CLAIM_LOCK:
        if _EXIT_HOOKED:
            return
        _EXIT_HOOKED = True
    from core.shutdown import register
    register("parser jobs", _stop_current_for_exit)


def _stop_current_for_exit() -> None:
    with _CLAIM_LOCK:
        job = _CURRENT
    if job is not None:
        job._stop_for_exit()


def start(settings: dict, *, label: str = "Парсер", kind: str = "run") -> ParserJob:
    """Start a parser run (raises :class:`ParserBusy` while another one is going)."""
    return ParserJob(settings, label=label, kind=kind).start()
