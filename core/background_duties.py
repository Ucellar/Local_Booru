"""What the program does by itself while it runs.  It lived in the timers of the old window and of its page
«Подписки»; the new window (``ui/web_shell.py``) has no Qt pages to keep them, so they are here:

- every 5 minutes: «Новые» older than their time go to the archive, and the trash lets go of what is older than
  its days (``archive_expired_inbox``, ``purge_expired_trash`` — the files being sent are held first; it stops
  between cards at the exit); not while a job of «Обслуживание» has the library, nor while the base is read-only;
- every minute: the subscriptions that are due are checked (``subscription_jobs.start_due``; after «Остановить»
  the check rests, as it did; not while the base is read-only);
- every minute: when the base's write-ahead log has grown and the person is idle, a passive checkpoint
  (``idle_wal_checkpoint``; the cadence and the threshold are in «Настройки → База»); it never waits for another
  maintenance job — the next minute tries again;
- every minute: a background task that has stalled is said in the log (once per stall);
- every 10 minutes: Брак entries the «фото или рисунок» model has not answered yet are checked, up to 200 at a time
  (``visual_work.background_round``: its weights are downloaded first when «скачивать самой» is on; the old
  window did this when its page «Брак» was opened); not while «Обслуживание» has the library nor while the base is
  read-only.  A round works 20 seconds at most (the other duties wait for it); one cut short — a download goes in
  slices, it resumes — goes on 30 seconds later;
- every 2 minutes: the length of video cards from before it was kept (``video_probe.backfill``: the gallery leaves
  the long ones to the tab «Видео»), 15 seconds at most a round, the rest 5 seconds later; not while «Обслуживание»
  has the library nor while the base is read-only;
- every 2 minutes: the sites are asked once about the posts of the cards from before (``site_posts.backfill``: who
  uploaded a post, its parent — what сборки are made of), 20 seconds at most a round, the rest 15 seconds later;
  while the parser or the subscriptions use the sites — 6 seconds a round, every 45 seconds (v5.44.4: they shared
  the sites' pace; before, nothing was asked, and a parser's run of days left the cards from before out of сборки);
  not while «Обслуживание» has the library, nor while the base is read-only;
- every 3 minutes: the сборки are put together again when the library changed (``card_sets.rebuild``; the сборки
  the person changed or made are left as they are); not while «Обслуживание» has the library.
- every 3 minutes: a card whose file is a site's reduced copy of its post (``sample_…``) gets the post's original in its
  place (``reduced_copies.background_round``, v5.44.13: the site is asked, the original downloaded, checked to be the
  same picture and larger, swapped in), 40 seconds at most a round, the rest 20 seconds later; while the parser or the
  subscriptions use the sites — 8 seconds a round, every minute; not while the session's downloads are off, nor while
  «Обслуживание» has the library, nor while the base is read-only;
- every 10 minutes: e621's export of posts as a local MD5 index (``e621_export.tick``, v5.44.25 — when «MD5 на e621 —
  по его выгрузке» is on): downloaded when missing or old, the posts uploaded after it caught up every hour — in a
  thread of its own (this one never waits for it), which stops at the exit and when the switch is turned off;
- every 3 minutes: the openings and endings of the series of the tab «Видео» whose episodes are new
  (``video_skips.background_round``: their sound and key frames compared episode to episode), 45 seconds at most a
  round, the rest 10 seconds later; not while a video plays in the window (it would stutter);
- every minute: the tags' categories by what most sites say (``tag_consensus``, v5.44.49): once after the upgrade
  every tag (the library had kept the first category anyone gave a tag — «female» a character on every card), then the
  tags whose sites' answers may have changed it (8 seconds at most a round); not while «Обслуживание» has the library,
  nor while the base is read-only.
- every 10 minutes: the new chapters of the titles «Скачать манхву» downloaded (``manhwa_download.watch_round``,
  v5.44.60): when it is due — at night once a day, any time when the last round is a day old (the computer was off at
  night) — a page task of its own (``manhwa_download.TASK``: the page «Скачать манхву» shows it, «Остановить» stops it) asks
  the sites and downloads the free chapters the folders have not, the paid ones that became free too; a download the
  person started goes first (the round waits for its end; (v5.44.74) and for the queue of them — ``manhwa_queue``,
  which goes on after the round).
- (v5.44.81) every 10 minutes: «Скачать → Из архива» knows a few titles of the manga and manhwa folders by itself
  (``manga_archive.background_round``: the new ones, the ones SauceNAO put off, «не нашлось» again after a week — a
  page task of its own, «Узнаю тайтлы в фоне»; the folders are looked at in a thread of its own, so this one never
  waits for them); not when «Узнавать само» is off, while the page's own work or a download of manga goes.
- (v5.44.111) every 5 minutes: the comics of the gallery (a pool of e621, a сборка of a comic of 15 pages and more)
  and the manga of the parser's folder — to the manga folder (``comic_manga.background_round``: copied and checked,
  the cards to the trash «перенесено в мангу»; 45 seconds a round, again in 20 when more waits); not while
  «Обслуживание» has the library, nor while the base is read-only.

One daemon thread, so none of this waits in the window's thread (the old window's timers wrote to the base from
it, and a VACUUM of «Обслуживание» froze the window for the base's busy time).  It ends with the program: the exit
waits for it as for the program's other threads (``core.shutdown.join_qthreads`` — it speaks their language), and an
exit that could not stop it is not a clean one (no checkpoint, no copy over a base still being written).  The old
window keeps its own timers and does not start these.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

_LOG = logging.getLogger("local_booru.duties")

INTERVALS = {                        # seconds between the runs of each duty
    "lifecycle": 5 * 60,
    "subscriptions": 60,
    "checkpoint": 60,
    "stalls": 60,
    "visual": 10 * 60,
    "durations": 2 * 60,
    "site_posts": 2 * 60,
    "card_sets": 3 * 60,
    "originals": 3 * 60,
    "skips": 3 * 60,
    "e621_export": 10 * 60,
    "tag_categories": 60,
    "manhwa": 10 * 60,
    "phone": 10 * 60,
    "manga_archive": 10 * 60,
    "comic_manga": 5 * 60,
}
FIRST_RUN = {"lifecycle": 1.5, "subscriptions": 60, "checkpoint": 30, "stalls": 60, "visual": 45,
             "durations": 20, "site_posts": 75, "card_sets": 25, "originals": 150,
             "skips": 90, "e621_export": 60, "tag_categories": 15, "manhwa": 120,
             "phone": 8, "manga_archive": 240, "comic_manga": 200}              # seconds after the start
VISUAL_AGAIN = 30                    # seconds to the next round of «фото или рисунок» when the last one was cut short
DURATIONS_AGAIN = 5                  # seconds to the next round of video lengths when cards are still waiting
SITE_POSTS_AGAIN = 15                # seconds to the next round of questions to the sites when posts are still waiting
SITE_POSTS_SHARED_AGAIN = 45         # …while the parser or the subscriptions use the sites (v5.44.4)
SKIPS_AGAIN = 10                     # seconds to the next round of openings and endings when episodes still wait
ORIGINALS_AGAIN = 20                 # seconds to the next round of originals when reduced copies still wait
ORIGINALS_SHARED_AGAIN = 60          # …while the parser or the subscriptions use the sites
COMIC_MANGA_AGAIN = 20               # seconds to the next round of comics to the manga folder when more wait
TICK = 1.0

_LOCK = threading.Lock()
_DUTIES: "BackgroundDuties | None" = None


class BackgroundDuties:
    """``settings()`` — the program's live settings (read anew for every run); ``hold(paths)`` — a context in which
    nothing sends those files (the web server's ``FILE_STREAMS.holding``); ``changed()`` — the library changed
    (cards archived or purged: the pages' cached tag counts go)."""

    def __init__(self, settings: Callable[[], dict], *, hold: Callable | None = None,
                 changed: Callable[[], None] | None = None, clock: Callable[[], float] = time.monotonic):
        self._settings = settings
        self._hold = hold
        self._changed = changed
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._checkpoint_at = 0.0
        self._stalls_said: dict = {}
        self._next_in: dict = {}         # duty -> seconds to its next run, when a run asked for one sooner
        self.last_runs: dict = {}        # duty -> time.time() of its last run that did not fail
        self.started_jobs: list = []     # ids of the subscription checks started here

    def start(self) -> "BackgroundDuties":
        self._thread = threading.Thread(target=self._loop, name="background-duties", daemon=True)
        self._thread.start()
        return self

    def stop(self, wait: float = 0.0) -> None:
        self._stop.set()
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(wait)

    # what ``core.shutdown.join_qthreads`` asks of a thread it waits for at the exit
    def isRunning(self) -> bool:  # noqa: N802 - the QThread protocol
        return self._thread is not None and self._thread.is_alive()

    def requestInterruption(self) -> None:  # noqa: N802
        self._stop.set()

    def wait(self, msecs: int | None = None) -> bool:
        if self._thread is None or self._thread is threading.current_thread():
            return True
        self._thread.join(None if msecs is None else max(0.0, msecs / 1000.0))
        return not self._thread.is_alive()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _loop(self) -> None:
        start = self._clock()
        due = {name: start + FIRST_RUN[name] for name in INTERVALS}
        while not self._stop.wait(TICK):
            self.run_due(due)

    def run_due(self, due: dict) -> None:
        """Run the duties whose time has come (``due``: duty -> when, moved on by this call); a failure of one is
        said in the log and tried again at its next time."""
        now = self._clock()
        for name in INTERVALS:
            if self._stop.is_set():
                return
            if now < due[name]:
                continue
            due[name] = now + INTERVALS[name]
            try:
                getattr(self, "_" + name)(dict(self._settings() or {}))
                self.last_runs[name] = time.time()
            except Exception as exc:        # the next run tries again; the thread goes on
                _LOG.warning("background duty %s failed: %s: %s", name, type(exc).__name__, exc)
            sooner = self._next_in.pop(name, None)
            if sooner is not None:
                due[name] = now + sooner

    # ── the duties ─────────────────────────────────────────────────────────
    def _stopping(self) -> bool:
        from core.shutdown import is_shutting_down
        return self._stop.is_set() or is_shutting_down()

    def _lifecycle(self, settings: dict) -> None:
        from core.database.connection import writes_blocked
        from core.library_lifecycle import archive_expired_inbox, purge_expired_trash
        if self._stopping() or writes_blocked() or _library_taken():
            return
        from core.trash_tools import restoring
        archived = int(archive_expired_inbox(settings) or 0)
        # between cards it gives way to the exit, to a job of «Обслуживание» that took the library meanwhile and to
        # «Вернуть всё» of the trash (the cards it has not reached yet would be gone)
        purged = {} if restoring() else purge_expired_trash(
            settings, hold=self._hold, stop_check=lambda: self._stopping() or _library_taken() or restoring()) or {}
        removed = int(purged.get("removed_records", 0) or 0)
        if archived or removed:
            _LOG.info("lifecycle: %d new card(s) archived, %d trash card(s) purged", archived, removed)
            if self._changed is not None:
                self._changed()

    def _subscriptions(self, settings: dict) -> None:
        from core import subscription_jobs
        from core.database.connection import writes_blocked
        if self._stopping() or writes_blocked():
            return
        # the check keeps its own copy: the running program's «_…» marks would stay in it as of this moment
        job = subscription_jobs.start_due({k: v for k, v in settings.items() if not str(k).startswith("_")})
        if job is not None:
            self.started_jobs.append(job.id)
            _LOG.info("subscriptions: automatic check of %d subscription(s) started", len(job.subs))

    def _checkpoint(self, settings: dict) -> None:
        """At most one passive checkpoint per interval, only when the log has grown and the person is idle."""
        if not bool(settings.get("sqlite_idle_checkpoint_enabled", True)):
            return
        interval = max(60, int(settings.get("sqlite_idle_checkpoint_interval_seconds", 300) or 300))
        now = self._clock()
        if self._checkpoint_at and now - self._checkpoint_at < interval:
            return
        from core.idle_wal_checkpoint import run_idle_passive_checkpoint, wal_checkpoint_probe
        if not wal_checkpoint_probe(settings).get("needed"):
            return
        from core.maintenance_coordinator import MaintenanceCancelled, get_maintenance_coordinator
        coordinator = get_maintenance_coordinator()
        min_idle = max(0.0, float(settings.get("sqlite_idle_checkpoint_min_idle_seconds", 15) or 15))
        if not coordinator.can_run(settings, mode="idle", min_idle_seconds=min_idle).allowed:
            return

        def give_way() -> bool:
            """Never wait for another maintenance job (a VACUUM takes minutes), nor at the exit."""
            return self._stop.is_set() or coordinator.active_maintenance_job() not in ("", "wal_checkpoint")
        try:
            result = run_idle_passive_checkpoint(settings, stop_check=give_way)
        except MaintenanceCancelled:
            _LOG.debug("idle checkpoint gave way to %s", coordinator.active_maintenance_job() or "the exit")
            return
        if result.get("attempted"):          # one that gave way or found nothing to do tries again next minute
            self._checkpoint_at = now

    def _visual(self, settings: dict) -> None:
        """Брак, «фото или рисунок»: what the chosen model has not answered yet; it gives way at the exit and to a job
        of «Обслуживание» between pictures."""
        from core.database.connection import writes_blocked
        if self._stopping() or writes_blocked() or _library_taken():
            return
        from core import visual_work
        result = visual_work.background_round(settings, stop_check=lambda: self._stopping() or _library_taken())
        if result and result.get("more"):           # cut short by its time: the rest soon, in slices
            self._next_in["visual"] = VISUAL_AGAIN

    def _durations(self, settings: dict) -> None:
        """The length of video cards from before it was kept at import; it gives way at the exit and to a job of
        «Обслуживание» between cards."""
        from core.database.connection import writes_blocked
        if self._stopping() or writes_blocked() or _library_taken():
            return
        from core import video_probe
        result = video_probe.backfill(settings, stop_check=lambda: self._stopping() or _library_taken())
        if result.get("done"):
            _LOG.info("video lengths: %d card(s) looked at%s", result["done"], ", more to come" if result.get("more") else "")
        if result.get("more"):
            self._next_in["durations"] = DURATIONS_AGAIN

    def _site_posts(self, settings: dict) -> None:
        """The sites asked once about the posts of the cards from before (who uploaded them, their parents); it gives
        way at the exit and to a job of «Обслуживание» between requests."""
        from core.database.connection import writes_blocked
        if self._stopping() or writes_blocked() or _library_taken():
            return
        from core import site_posts
        result = site_posts.backfill(settings, stop_check=lambda: self._stopping() or _library_taken())
        if result.get("asked"):
            _LOG.info("site posts: %d asked, %d card row(s) kept%s%s", result["asked"], result["written"],
                      f", {result['mended']} card(s) got their site's tags back" if result.get("mended") else "",
                      ", more to come" if result.get("more") else "")
        if result.get("more") and result.get("asked"):
            self._next_in["site_posts"] = SITE_POSTS_SHARED_AGAIN if result.get("busy") else SITE_POSTS_AGAIN

    def _originals(self, settings: dict) -> None:
        """A card whose file is a site's reduced copy of its post gets the post's original (``core/reduced_copies``);
        the gallery lets go of the file meanwhile, it gives way at the exit and to a job of «Обслуживание»."""
        from core.database.connection import writes_blocked
        if self._stopping() or writes_blocked() or _library_taken():
            return
        from core import reduced_copies
        result = reduced_copies.background_round(settings, hold=self._hold,
                                                 stop_check=lambda: self._stopping() or _library_taken())
        if result.get("looked"):
            _LOG.info("originals: %d card(s) looked at, %d got the original%s", result["looked"], result["done"],
                      ", more to come" if result.get("more") else "")
            if result.get("done") and self._changed is not None:
                self._changed()
        if result.get("more"):
            self._next_in["originals"] = ORIGINALS_SHARED_AGAIN if result.get("busy") else ORIGINALS_AGAIN

    def _skips(self, settings: dict) -> None:
        """The openings and endings of series' new episodes, by their sound and pictures (``core/video_skips``); not
        while a video plays in the window or a download writes the series folders, stopped at the exit."""
        if self._stopping() or _watching() or _downloading():
            return
        from core import video_skips
        result = video_skips.background_round(settings, stop=lambda: self._stopping() or _watching() or _downloading())
        if result.get("done"):
            _LOG.info("openings and endings: %d episode(s) looked at%s", result["done"],
                      ", more to come" if result.get("more") else "")
        if result.get("more"):
            self._next_in["skips"] = SKIPS_AGAIN

    def _e621_export(self, settings: dict) -> None:
        """e621's export as a local MD5 index (``core/e621_export``): the work, when something is due, starts in a
        thread of its own; it reads the live switch (turned off — it stops)."""
        if self._stopping():
            return
        from core import e621_export
        if e621_export.tick(settings, live=self._settings):
            _LOG.info("e621 export: work started")

    def _tag_categories(self, settings: dict) -> None:
        """The tags' categories by what most sites say (``core/database/tag_consensus``): every tag once after the
        upgrade, then the tags whose sites' answers may have changed it."""
        from core.database.connection import writes_blocked
        if self._stopping() or writes_blocked() or _library_taken():
            return
        from core.database import tag_consensus
        changed = False
        if not tag_consensus.repaired(settings):
            summary = tag_consensus.repair(settings, log=_LOG.info, stop=self._stopping)
            changed = bool(summary.get("changed") or summary.get("filled_fixed") or summary.get("slips_fixed"))
        if self._stopping():
            return
        result = tag_consensus.review(settings, budget=8.0, log=_LOG.info)
        if (changed or result.get("changed")) and self._changed is not None:
            self._changed()

    def _manhwa(self, settings: dict) -> None:
        """(v5.44.60) The new chapters of the downloaded manhwa, once a day: a page task of its own, so this thread
        never waits for the sites."""
        if self._stopping():
            return
        from core import manhwa_download, manhwa_queue, page_jobs
        if not manhwa_download.due():
            return
        current = page_jobs.current(manhwa_download.TASK)
        if current is not None and current.running or manhwa_queue.waiting():
            return                                         # a download of the person's: the round waits for it
        live = self._settings

        def run(say, stop_check):
            try:
                return manhwa_download.watch_round(dict(live() or {}), say=say,
                                                   stop_check=lambda: stop_check() or self._stopping())
            finally:
                if not stop_check():                       # (v5.44.74) a download asked meanwhile — now
                    manhwa_queue.next_soon()
        try:
            page_jobs.start(manhwa_download.TASK, "Новые главы манхвы", run)
        except page_jobs.TaskBusy:
            return
        _LOG.info("manhwa: the round of new chapters started")

    def _manga_archive(self, settings: dict) -> None:
        """(v5.44.81) «Из архива» by itself: a round of a few titles, as a page task of its own."""
        if self._stopping():
            return
        from core import manga_archive
        manga_archive.background_round(settings, live=self._settings)   # it says in the log when a round begins

    def _phone(self, settings: dict) -> None:
        """(v5.44.64) The owner's phone let in (``core/phone_access``): Tailscale still takes its address to this run's
        port — another one after a busy port, the computer renamed in the tailnet; (v5.44.71) through the owner's
        server — the connection to it is there (``core/phone_relay``: it makes itself again when lost; this only
        starts it after the start of the program and stops it when switched off)."""
        if self._stopping():
            return
        from webui import service
        server = service.running()
        if server is None:
            return
        from core import phone_relay
        phone_relay.keep(settings, server.port)
        if not settings.get("phone_access"):
            return
        from core import phone_access
        changes = phone_access.keep(settings, server.port)
        if changes:
            from webui import settings_store
            settings_store.save(changes)
            _LOG.info("phone: the computer's name or account in Tailscale changed: %s", sorted(changes))

    def _comic_manga(self, settings: dict) -> None:
        """(v5.44.111) The comics of the gallery and the manga of the parser's folder to the manga folder
        (``core/comic_manga``); not while «Обслуживание» has the library, stopped at the exit."""
        from core.database.connection import writes_blocked
        if self._stopping() or writes_blocked() or _library_taken():
            return
        from core import comic_manga
        result = comic_manga.background_round(settings, stop_check=lambda: self._stopping() or _library_taken())
        if result.get("moved") or result.get("copied"):
            _LOG.info("comics and manga to the manga folder: %s", ", ".join(result["moved"] + result["copied"]))
            if self._changed is not None:
                self._changed()
        if result.get("more"):
            self._next_in["comic_manga"] = COMIC_MANGA_AGAIN

    def _card_sets(self, settings: dict) -> None:
        """The сборки put together again when the library changed since the last time."""
        if self._stopping() or _library_taken():
            return
        from core import card_sets
        card_sets.rebuild(settings)

    def _stalls(self, settings: dict) -> None:
        """A background task with no sign of life for long is said in the log, once per stall."""
        from core.background_tasks import get_background_task_registry
        registry = get_background_task_registry()
        minutes = max(2.0, float(settings.get("background_task_stall_minutes", 10) or 10))
        waiting = max(minutes, float(settings.get("background_task_waiting_stall_minutes", 60) or 60))
        stale = registry.stale_snapshot(minutes * 60.0, leaf_only=True, waiting_threshold_seconds=waiting * 60.0)
        live = {str(row.get("task_id") or "") for row in registry.active_snapshot(include_parents=True)}
        self._stalls_said = {k: v for k, v in self._stalls_said.items() if k in live}
        for row in stale:
            task_id, mark = str(row.get("task_id") or ""), float(row.get("last_activity_at") or 0)
            if self._stalls_said.get(task_id) == mark:
                continue
            self._stalls_said[task_id] = mark
            _LOG.warning("BACKGROUND TASK STALL: id=%s kind=%s name=%s no activity for %.1f min; thread=%s; detail=%s",
                         task_id, row.get("kind", "background"), row.get("name", "?"),
                         float(row.get("effective_idle_seconds") or 0) / 60.0, row.get("thread_name", "?"),
                         row.get("detail", ""))


def _watching() -> bool:
    """A video plays in the window now: in the page (its frames go to it) or full size."""
    try:
        from webui.video_relay import RELAY
    except ImportError:
        return False
    return RELAY.playing()


def _downloading() -> bool:
    """«Скачать с сайта» downloads into the video folder now (and puts voice-overs together: files are replaced)."""
    from core import video_download
    return video_download.busy()


def _library_taken() -> bool:
    """A job of «Обслуживание» or «Удаление и сброс» has the library to itself (it took the parser): wait for it."""
    from core.parser import jobs as parser_jobs
    return parser_jobs.owner().startswith(parser_jobs.MAINTENANCE_OWNER.split("«")[0])


def start(settings: Callable[[], dict], *, hold: Callable | None = None,
          changed: Callable[[], None] | None = None) -> BackgroundDuties:
    """Start the duties for this run of the program (once; a second call returns the running ones).  The exit stops
    them first and then waits for them with the program's other threads (``join_qthreads``)."""
    global _DUTIES
    with _LOCK:
        if _DUTIES is not None and _DUTIES.running:
            return _DUTIES
        _DUTIES = BackgroundDuties(settings, hold=hold, changed=changed).start()
        duties = _DUTIES
    from core.shutdown import register, register_qthread
    register("background duties", duties.stop)
    register_qthread(duties, "background duties")
    return duties


def current() -> BackgroundDuties | None:
    with _LOCK:
        return _DUTIES


def running() -> bool:
    duties = current()
    return duties is not None and duties.running


def stop() -> None:
    duties = current()
    if duties is not None:
        duties.stop()
