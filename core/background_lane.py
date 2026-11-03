"""Reusable durable background-lane primitive.

The primitive centralises the hard parts learned from reverse retry: durable
keyset paging, one eligibility classifier for both pending and drain, explicit
waiting/running registry states, bounded no-progress backoff, cancellation and
trace hooks. Feature lanes provide only classify/execute/retry policy callbacks.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from core.database.connection import db


ACTIVE_JOB_STATES = {"pending", "waiting", "retry"}
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reason: str = "eligible"
    detail: str = ""


@dataclass
class LaneJob:
    job_id: int
    lane: str
    identity_key: str
    payload: dict[str, Any]
    state: str
    priority: int
    retry_after: int
    attempts: int
    queued_at: int
    updated_at: int
    owner: str
    generation: int
    last_error: str
    dedupe_key: str


@dataclass
class DrainReport:
    lane: str
    fetched: int = 0
    pages: int = 0
    eligible: int = 0
    admitted: int = 0
    exhausted: bool = False
    reasons: dict[str, int] = field(default_factory=dict)


Classifier = Callable[[LaneJob, Any], EligibilityResult]
Executor = Callable[[LaneJob, Any], Any]
RetryPolicy = Callable[[LaneJob, BaseException], tuple[bool, int, str]]


def _row_to_job(row) -> LaneJob:
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except Exception:
        payload = {}
    return LaneJob(
        job_id=int(row["job_id"]), lane=str(row["lane"]), identity_key=str(row["identity_key"]),
        payload=payload, state=str(row["state"]), priority=int(row["priority"] or 0),
        retry_after=int(row["retry_after"] or 0), attempts=int(row["attempts"] or 0),
        queued_at=int(row["queued_at"] or 0), updated_at=int(row["updated_at"] or 0),
        owner=str(row["owner"] or ""), generation=int(row["generation"] or 0),
        last_error=str(row["last_error"] or ""), dedupe_key=str(row["dedupe_key"] or ""),
    )


class BackgroundLane:
    def __init__(
        self,
        settings: dict,
        lane: str,
        *,
        classifier: Classifier | None = None,
        executor: Executor | None = None,
        retry_policy: RetryPolicy | None = None,
        trace: Callable[[str], Any] | None = None,
        registry_task_id: str = "",
        page_size: int = 500,
        empty_backoff_seconds: float = 5.0,
        stale_running_seconds: int | None = None,
    ) -> None:
        self.settings = settings
        self.lane = str(lane or "background")
        self.classifier = classifier or (lambda _job, _ctx: EligibilityResult(True))
        self.executor = executor
        self.retry_policy = retry_policy or self._default_retry_policy
        self.trace = trace or (lambda _message: None)
        self.registry_task_id = str(registry_task_id or "")
        self.page_size = max(10, min(5000, int(page_size or 500)))
        self.empty_backoff_seconds = max(0.05, min(300.0, float(empty_backoff_seconds or 5.0)))
        if stale_running_seconds is None:
            stale_running_seconds = int((settings or {}).get("background_lane_stale_running_seconds", 3600) or 3600)
        self.stale_running_seconds = max(1, min(7 * 24 * 3600, int(stale_running_seconds)))
        self.owner = f"{self.lane}-{uuid.uuid4().hex[:12]}"
        self.generation = int(time.time() * 1000)
        self._last_recovery_at = 0.0
        self._empty_said = False      # (v5.44.35) «EMPTY» said once until the lane has work again

    @staticmethod
    def _default_retry_policy(job: LaneJob, exc: BaseException) -> tuple[bool, int, str]:
        delay = min(3600, max(5, 5 * (2 ** min(8, int(job.attempts or 0)))))
        return True, delay, f"{type(exc).__name__}: {exc}"

    def enqueue(self, identity_key: str, payload: dict[str, Any] | None = None, *, priority: int = 0, retry_after: int = 0, dedupe_key: str = "") -> int:
        now = int(time.time())
        with db(self.settings, write=True) as con:
            con.execute(
                """INSERT INTO background_lane_jobs(lane,identity_key,payload_json,state,priority,retry_after,attempts,queued_at,updated_at,owner,generation,last_error,dedupe_key)
                   VALUES(?,?,?,'pending',?,?,0,?,?,'',0,'',?)
                   ON CONFLICT(lane,identity_key) DO UPDATE SET
                     payload_json=excluded.payload_json,
                     priority=MAX(background_lane_jobs.priority,excluded.priority),
                     retry_after=MIN(background_lane_jobs.retry_after,excluded.retry_after),
                     state=CASE WHEN background_lane_jobs.state IN ('completed','cancelled') THEN 'pending' ELSE background_lane_jobs.state END,
                     updated_at=excluded.updated_at,
                     dedupe_key=CASE WHEN excluded.dedupe_key!='' THEN excluded.dedupe_key ELSE background_lane_jobs.dedupe_key END""",
                (self.lane, str(identity_key), json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), int(priority), int(retry_after), now, now, str(dedupe_key or "")),
            )
            row = con.execute("SELECT job_id FROM background_lane_jobs WHERE lane=? AND identity_key=?", (self.lane, str(identity_key))).fetchone()
            return int(row[0])

    def _page(self, con, *, now: int, cursor: tuple[int, int, int, int] | None = None) -> list[LaneJob]:
        params: list[Any] = [self.lane, int(now)]
        cursor_sql = ""
        if cursor is not None:
            p, retry_after, queued_at, job_id = cursor
            cursor_sql = """ AND (
                priority < ? OR
                (priority=? AND retry_after>?) OR
                (priority=? AND retry_after=? AND queued_at>?) OR
                (priority=? AND retry_after=? AND queued_at=? AND job_id>?)
            )"""
            params.extend([p, p, retry_after, p, retry_after, queued_at, p, retry_after, queued_at, job_id])
        params.append(self.page_size)
        rows = con.execute(
            """SELECT * FROM background_lane_jobs
               WHERE lane=? AND state IN ('pending','waiting','retry') AND retry_after<=?"""
            + cursor_sql
            + " ORDER BY priority DESC,retry_after,queued_at,job_id LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_row_to_job(row) for row in rows]

    @staticmethod
    def _cursor(job: LaneJob) -> tuple[int, int, int, int]:
        return (int(job.priority), int(job.retry_after), int(job.queued_at), int(job.job_id))

    def scan_eligible(self, context: Any = None, *, target: int = 1, now: int | None = None) -> tuple[list[LaneJob], DrainReport]:
        now_i = int(now or time.time())
        report = DrainReport(self.lane)
        selected: list[LaneJob] = []
        cursor = None
        with db(self.settings, readonly=True) as con:
            while len(selected) < max(1, int(target or 1)):
                page = self._page(con, now=now_i, cursor=cursor)
                report.pages += 1
                if not page:
                    report.exhausted = True
                    break
                report.fetched += len(page)
                for job in page:
                    decision = self.classifier(job, context)
                    reason = str(decision.reason or ("eligible" if decision.eligible else "ineligible"))
                    report.reasons[reason] = int(report.reasons.get(reason, 0)) + 1
                    if decision.eligible:
                        report.eligible += 1
                        selected.append(job)
                        if len(selected) >= max(1, int(target or 1)):
                            break
                cursor = self._cursor(page[-1])
                if len(page) < self.page_size:
                    report.exhausted = True
                    break
        return selected, report

    def pending_state(self, context: Any = None) -> tuple[LaneJob | None, DrainReport]:
        jobs, report = self.scan_eligible(context, target=1)
        return (jobs[0] if jobs else None), report

    def claim(self, job: LaneJob) -> bool:
        now = int(time.time())
        with db(self.settings, write=True) as con:
            cur = con.execute(
                """UPDATE background_lane_jobs SET state='running',owner=?,generation=?,attempts=attempts+1,updated_at=?
                   WHERE job_id=? AND lane=? AND state IN ('pending','waiting','retry')""",
                (self.owner, self.generation, now, int(job.job_id), self.lane),
            )
            return int(cur.rowcount or 0) == 1

    def heartbeat(self, job_id: int) -> bool:
        """Refresh a running lease owned by this lane instance.

        Long executors should call this between bounded work chunks. It prevents
        another process/instance from recovering a legitimately active job.
        """
        with db(self.settings, write=True) as con:
            cur = con.execute(
                """UPDATE background_lane_jobs SET updated_at=?
                   WHERE job_id=? AND lane=? AND state='running' AND owner=? AND generation=?""",
                (int(time.time()), int(job_id), self.lane, self.owner, self.generation),
            )
            return int(cur.rowcount or 0) == 1

    def recover_stale_running(self, *, now: int | None = None) -> int:
        """Return abandoned running jobs to retry after a bounded lease timeout.

        Recovery never steals a job owned by this exact instance/generation.
        Across a restart the owner changes, so an orphan becomes eligible once
        its last heartbeat is older than ``stale_running_seconds``.
        """
        now_i = int(now or time.time())
        cutoff = now_i - int(self.stale_running_seconds)
        with db(self.settings, write=True) as con:
            cur = con.execute(
                """UPDATE background_lane_jobs
                   SET state='retry', owner='', generation=0,
                       retry_after=CASE WHEN retry_after>? THEN ? ELSE retry_after END,
                       updated_at=?,
                       last_error=CASE
                           WHEN last_error='' THEN 'recovered stale running lease'
                           ELSE last_error || '; recovered stale running lease'
                       END
                   WHERE lane=? AND state='running' AND updated_at<=?
                     AND NOT(owner=? AND generation=?)""",
                (now_i, now_i, now_i, self.lane, cutoff, self.owner, self.generation),
            )
            recovered = int(cur.rowcount or 0)
        if recovered:
            self.trace(
                f"BACKGROUND LANE RECOVER: lane={self.lane} recovered={recovered} "
                f"stale_after={self.stale_running_seconds}s"
            )
        return recovered

    def complete(self, job_id: int) -> None:
        with db(self.settings, write=True) as con:
            con.execute("UPDATE background_lane_jobs SET state='completed',owner='',updated_at=?,last_error='' WHERE job_id=? AND lane=?", (int(time.time()), int(job_id), self.lane))

    def retry(self, job_id: int, *, delay_seconds: int, error: str) -> None:
        now = int(time.time())
        with db(self.settings, write=True) as con:
            con.execute(
                "UPDATE background_lane_jobs SET state='retry',owner='',retry_after=?,updated_at=?,last_error=? WHERE job_id=? AND lane=?",
                (now + max(1, int(delay_seconds)), now, str(error or "")[:4000], int(job_id), self.lane),
            )

    def cancel(self, job_id: int) -> None:
        with db(self.settings, write=True) as con:
            con.execute("UPDATE background_lane_jobs SET state='cancelled',owner='',updated_at=? WHERE job_id=? AND lane=?", (int(time.time()), int(job_id), self.lane))

    def _registry(self, *, state: str, detail: str, progress_text: str) -> None:
        if not self.registry_task_id:
            return
        try:
            from core.background_tasks import get_background_task_registry
            get_background_task_registry().set_state(self.registry_task_id, state, detail=detail, progress_text=progress_text)
        except Exception:
            pass

    def drain_once(self, context: Any = None, *, target: int = 1, should_stop: Callable[[], bool] | None = None) -> DrainReport:
        # A cheap periodic lease recovery makes durable lanes restart-safe
        # without scanning/updating on every tight loop iteration.
        mono = time.monotonic()
        recovery_interval = max(1.0, min(60.0, self.stale_running_seconds / 4.0))
        if mono - self._last_recovery_at >= recovery_interval:
            self.recover_stale_running()
            self._last_recovery_at = mono
        jobs, report = self.scan_eligible(context, target=target)
        if not jobs:
            self._registry(state="waiting", detail=f"{self.lane}-no-eligible", progress_text=f"{self.lane}: waiting; fetched={report.fetched}")
            # (v5.44.35) once until the lane has work again, not every backoff: the owner's log of 2.11.2026 — 455 of its
            # 1037 lines were the family-completion lane saying «EMPTY» every 30 seconds for two hours
            if not self._empty_said:
                self._empty_said = True
                self.trace(f"BACKGROUND LANE EMPTY: lane={self.lane} fetched={report.fetched} pages={report.pages} reasons={report.reasons}; backoff={self.empty_backoff_seconds:.2f}s (said once until there is work)")
            deadline = time.monotonic() + self.empty_backoff_seconds
            while time.monotonic() < deadline:
                if should_stop and should_stop():
                    break
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            return report
        self._empty_said = False
        self._registry(state="running", detail=f"{self.lane}-drain", progress_text=f"{self.lane}: processing {len(jobs)}")
        for job in jobs:
            if should_stop and should_stop():
                break
            if not self.claim(job):
                report.reasons["claim_race"] = report.reasons.get("claim_race", 0) + 1
                continue
            report.admitted += 1
            try:
                if self.executor is not None:
                    self.executor(job, context)
                self.complete(job.job_id)
                self.trace(f"BACKGROUND LANE COMPLETE: lane={self.lane} job={job.job_id} key={job.identity_key}")
            except BaseException as exc:
                do_retry, delay, reason = self.retry_policy(job, exc)
                if do_retry:
                    self.retry(job.job_id, delay_seconds=delay, error=reason)
                    self.trace(f"BACKGROUND LANE RETRY: lane={self.lane} job={job.job_id} delay={delay}s error={reason}")
                else:
                    with db(self.settings, write=True) as con:
                        con.execute("UPDATE background_lane_jobs SET state='failed',owner='',updated_at=?,last_error=? WHERE job_id=?", (int(time.time()), str(reason)[:4000], int(job.job_id)))
                    self.trace(f"BACKGROUND LANE FAILED: lane={self.lane} job={job.job_id} error={reason}")
        return report
