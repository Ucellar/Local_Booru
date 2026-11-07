"""(v5.44.74) The downloads of «Скачать манхву» one at a time — the ones asked meanwhile wait in a queue — and kept
between runs of the program, as «Скачать с сайта» keeps its own (``webui/pages/video_get.py``; the owner, 03.11.2026,
of the videos: «ещё бы оно очередь запоминало»; 5.11.2026 of the manhwa: «посмотри как сделано скачивание видео»).

«Скачать» while a download goes — another title's, or the nightly round of new chapters (``background_duties``) —
puts this one in the queue (:func:`start`): it begins by itself when the one before it ends (:func:`next_soon`).
«Остановить» stops the download and empties the queue (:func:`stop_all`).  The nightly round does not begin while
someone's download waits (:func:`waiting`), and the queue goes on after it.

``manhwa_queue.json`` in the settings folder keeps the download going and the queue — each as its title's address,
its translators, where it goes, the chapters chosen, the boxes, «Пауза».  After the program starts again
(``RESTORE_AFTER``) each one is found again by its address (:func:`work`: ``manhwa_download.look``) and goes on — the
chapters already in the folder are there, a chapter begun goes on from its strips; a pause stays a pause.  One ended
by «Остановить» is not kept; one ended by the program closing is.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from pathlib import Path

from core.manhwa_download import TASK

_LOG = logging.getLogger("local_booru.manhwa_get")
KEEP_NAME = "manhwa_queue.json"
RESTORE_AFTER = 30.0                   # seconds after the start: the program's own start goes first
_QUEUE: list = []                      # {"id", "find", "title", "count", "chosen", "label", "run", "record"}
_LOCK = threading.Lock()
_IDS = itertools.count(1)
_CURRENT: dict = {"record": None}      # the download going: what it is, to begin it again after a restart
_USER_STOP = threading.Event()         # «Остановить»: what it stops is not begun again
_RESTORED = threading.Event()          # the downloads of the last run read (once a run of the program)
_PENDING: dict = {"titles": [], "at": 0.0}


def record_of(found, *, chapters, where: str, follow: bool, helpers: bool) -> dict:
    """What a download is, to begin it again after a restart: its title's address on the site that gave it, its
    translators, where it goes, the chapters chosen (None — every new one), the boxes."""
    return {"url": found.title.url, "title": found.title.title, "branch": found.branch.id if found.branch else "",
            "where": where, "follow": bool(follow), "helpers": bool(helpers),
            "chapters": None if chapters is None else [str(c) for c in chapters]}


def _job(record: dict | None, work):
    """A download as the page task: kept as the one going while it goes; when it ends, the next of the queue starts.
    Ended by the program closing (its stop, not «Остановить») — kept, to be begun again at the next start."""
    def run(say, stop_check):
        _USER_STOP.clear()
        with _LOCK:
            _CURRENT["record"] = dict(record) if record is not None else None
        _persist()
        try:
            return work(say, stop_check)
        finally:
            leaving = stop_check() and not _USER_STOP.is_set()
            with _LOCK:
                if not leaving:
                    _CURRENT["record"] = None
            _persist()
            if not stop_check():                     # the next one in the queue, when this one has ended
                next_soon()
    return run


def start(label: str, work, *, record: dict | None = None, about=None, title: str = "", count: int = 0,
          chosen: list | None = None) -> dict:
    """The download begun — {"id": its task} — or, while another goes, put in the queue: {"queued": its place,
    "queue_id"} (the same list asked again keeps its place with its new choice)."""
    from core import page_jobs
    run = _job(record, work)
    try:
        started = page_jobs.start(TASK, label, run, about=about)
    except page_jobs.TaskBusy:
        with _LOCK:
            # (v5.44.107) the same title into the same folder waits already (its page opened again — another search):
            # one download of it, with this choice (the page joined the chapters of both — ``manhwa_get.download``)
            entry = next((q for q in _QUEUE if (about is not None and q["find"] == about) or _same(q, record)), None)
            if entry is None:
                entry = {"id": next(_IDS), "find": about}
                _QUEUE.append(entry)
            entry.update(title=title or label, count=int(count), chosen=list(chosen or []), label=label, run=run,
                         record=record)
            place = _QUEUE.index(entry) + 1
        _persist()
        going = page_jobs.current(TASK)              # one ending just now: the next starts (not after a stop)
        next_soon(after=going.id if going is not None else None)
        return {"queued": place, "queue_id": entry["id"]}
    return {"id": started.id}


def next_soon(after=None) -> None:
    threading.Thread(target=_next, kwargs={"after": after}, name="manhwa-download-queue", daemon=True).start()


def _next(wait: float = 10.0, after=None) -> None:
    """The first download of the queue started — once the one going has ended (the one that ends calls this before
    its task says so: it is waited for a while; one still going after that starts the queue itself at its end).
    ``after`` — the task going when one was put in the queue: stopped (the program closing) — nothing starts."""
    from core import page_jobs
    deadline = time.monotonic() + wait
    while True:
        task = page_jobs.current(TASK)
        if task is None or not task.running:
            break
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)
    if after is not None and task is not None and task.id == after and task.status == "stopped":
        return
    with _LOCK:
        if not _QUEUE:
            return
        entry = _QUEUE.pop(0)
    try:
        page_jobs.start(TASK, entry["label"], entry["run"], about=entry["find"])
    except page_jobs.TaskBusy:                       # another started meanwhile
        with _LOCK:
            _QUEUE.insert(0, entry)


def public() -> list:
    """The queue for the pages; (v5.44.107) ``url`` and ``where`` — which title it is (a title's page shows its own
    place in the queue: «В очереди 2-м»)."""
    with _LOCK:
        return [{"id": q["id"], "find": q["find"], "title": q["title"], "count": q.get("count", 0),
                 "chosen": list(q.get("chosen") or []), "url": str((q.get("record") or {}).get("url") or ""),
                 "where": str((q.get("record") or {}).get("where") or "")} for q in _QUEUE]


def _same(entry: dict, record: "dict | None") -> bool:
    """(v5.44.107) ``entry`` of the queue is a download of the title ``record`` is, into the same folder."""
    kept = entry.get("record") or {}
    return bool(record) and bool(kept.get("url")) and kept.get("url") == record.get("url") \
        and kept.get("where") == record.get("where")


def queued(url: str, where: str) -> dict | None:
    """(v5.44.107) The download of this title waiting in the queue: {"id", "place", "chapters" — the ones chosen (None —
    every new one)}; None — it does not wait."""
    with _LOCK:
        for place, q in enumerate(_QUEUE, 1):
            kept = q.get("record") or {}
            if kept.get("url") == url and kept.get("where") == where:
                chosen = kept.get("chapters")
                return {"id": q["id"], "place": place, "chapters": None if chosen is None else list(chosen)}
    return None


def waiting() -> bool:
    """Someone's downloads wait in the queue (the nightly round waits for them)."""
    with _LOCK:
        return bool(_QUEUE)


def unqueue(wanted) -> bool:
    """«Убрать» a download from the queue (it has not begun): False — it is not there (begun already)."""
    with _LOCK:
        entry = next((q for q in _QUEUE if q["id"] == wanted), None)
        if entry is not None:
            _QUEUE.remove(entry)
    _persist()
    return entry is not None


def stop_all() -> list:
    """«Остановить»: the queue emptied, the download going not kept (the page stops it) — the titles dropped."""
    _USER_STOP.set()
    with _LOCK:
        dropped = [q["title"] for q in _QUEUE]
        _QUEUE.clear()
        _CURRENT["record"] = None
    _persist()
    return dropped


def note_paused(paused: bool) -> None:
    """A pause stays a pause after a restart."""
    with _LOCK:
        if _CURRENT.get("record"):
            _CURRENT["record"] = dict(_CURRENT["record"], paused=bool(paused))
    _persist()


# ── kept between runs ──────────────────────────────────────────────────────────────────────────────────────────────
def _keep_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / KEEP_NAME


def _persist() -> None:
    """``manhwa_queue.json``: the download going (when it is to be begun again) and the queue after it."""
    from core.file_safety import write_json_kept
    with _LOCK:
        records = ([_CURRENT["record"]] if _CURRENT.get("record") else []) + \
            [q["record"] for q in _QUEUE if q.get("record")]
    try:
        write_json_kept(_keep_file(), json.dumps({"downloads": records}, ensure_ascii=False), dict)
    except OSError as exc:
        _LOG.warning("the queue of manhwa downloads not kept: %s", exc)


def kept_records() -> list:
    """The downloads ``manhwa_queue.json`` keeps (each with its title's address); [] when none or it cannot be read."""
    from core.file_safety import read_json_kept
    try:
        data = read_json_kept(_keep_file(), dict, what="manhwa queue")
    except OSError as exc:
        _LOG.warning("the queue of manhwa downloads cannot be read now: %s", exc)
        return []
    return [r for r in (data or {}).get("downloads") or [] if isinstance(r, dict)
            and str(r.get("url") or "").startswith(("http://", "https://"))]


def await_restore() -> bool:
    """The downloads of the last run, before :func:`restore` begins them (``RESTORE_AFTER`` from now): their titles
    kept for the page.  False — none."""
    if _RESTORED.is_set():
        return False
    records = kept_records()
    if not records:
        return False
    with _LOCK:
        _PENDING.update(titles=[str(r.get("title") or r["url"]) for r in records], at=time.monotonic() + RESTORE_AFTER)
    return True


def restoring_public() -> dict | None:
    """{"titles", "in": seconds} of the downloads to be begun again soon; None when none waits."""
    with _LOCK:
        if _RESTORED.is_set() or not _PENDING["titles"]:
            return None
        return {"titles": list(_PENDING["titles"]), "in": max(0, int(round(_PENDING["at"] - time.monotonic())))}


def restore_later(settings_of) -> None:
    """In a thread of its own: :func:`restore` after ``RESTORE_AFTER`` (``settings_of()`` — the settings then)."""
    def later():
        try:
            time.sleep(RESTORE_AFTER)
            restore(settings_of())
        except Exception as exc:   # noqa: BLE001 - a queue that is not restored: the program goes on without it
            _LOG.warning("the queue of manhwa downloads not restored: %s: %s", type(exc).__name__, exc)
    threading.Thread(target=later, name="manhwa-download-restore", daemon=True).start()


def restore(settings: dict) -> int:
    """The downloads of the last run (``manhwa_queue.json``) put in the queue again and the first begun.  Once a run.
    -> how many."""
    if _RESTORED.is_set():
        return 0
    _RESTORED.set()
    with _LOCK:
        _PENDING["titles"] = []
    records = kept_records()
    if not records:
        return 0
    with _LOCK:
        for n, record in enumerate(records):
            title = str(record.get("title") or record["url"])
            chosen = record.get("chapters")
            _QUEUE.append({"id": next(_IDS), "find": f"saved{n}", "title": title, "count": len(chosen or []),
                           "chosen": [], "label": f"Скачать: {title}",
                           "run": _job(record, lambda say, stop_check, record=record: work(record, settings, say,
                                                                                         stop_check)),
                           "record": record})
    _LOG.info("manhwa downloads of the last run: %d — begun again", len(records))
    _persist()
    _next()
    return len(records)


def work(record: dict, settings: dict, say, stop_check) -> dict:
    """A download kept from the last run: its title found again by its address, its chapters downloaded."""
    from core import manhwa_download as D, manhwa_sources as S
    title = record.get("title") or record["url"]
    say(f"С прошлого запуска программы: «{title}» — читаю его главы заново")
    net = S.Net(stop_check)
    try:
        found = D.look(str(record["url"]), settings, branch=str(record.get("branch") or ""), net=net,
                       where=str(record.get("where") or ""), say=say, helpers=bool(record.get("helpers", True)),
                       about=False)
    except D.DownloadError as exc:
        return {"failed": f"«{title}»: {exc}"}
    chapters = record.get("chapters")
    result = D.download(found, chapters=chapters if isinstance(chapters, list) else None,
                        follow=bool(record.get("follow", True)), helpers=bool(record.get("helpers", True)), net=net,
                        say=say, stop_check=stop_check, start_paused=bool(record.get("paused")))
    result["open"] = open_link(settings, result.get("folder") or "", result.get("kind") or "manhwa")
    return result


def open_link(settings: dict, folder: str, kind: str) -> str:
    """The page of the title downloaded into ``folder``."""
    if not folder:
        return ""
    if kind == "manga":
        from core import manga_library as M
        root = M.manga_root(settings)
        for manga in M.LIBRARY.items(str(root), refresh=True) if root else []:
            if Path(manga.path) == Path(folder):
                return f"/manga/g/{manga.id}"
        return "/manga"
    from core import manhwa_library as H
    for title in H.LIBRARY.items(H.manhwa_root(settings), refresh=True):
        if Path(title.path) == Path(folder):
            return f"/manhwa/t/{title.id}"
    return "/manhwa"
