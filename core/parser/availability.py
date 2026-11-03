"""Per-run availability of one booru site or reverse-search service.

There are two different kinds of "not available", and they must not be mixed up:

* permanent (``disable``) — needs the user's action: missing or wrong API key,
  a Cloudflare wall, an API that keeps rejecting our requests.  The source is
  skipped for the rest of the run and its silence does NOT hold files back:
  they may go to Брак, and the run summary tells the user to re-check Брак
  after fixing the source.

  When the person has fixed it (refreshed the cookies after a Cloudflare check,
  written the key) the page «Парсер» asks the source once more («Проверить снова»,
  core/parser/runner.py ``site_again``); it answers — ``enable_again``: the files read
  from then on ask it again, those that went on without it are left for «Добор».

* temporary (pause) — timeouts, DNS errors, HTTP 429/5xx, maintenance pages.
  After ``FAILS_BEFORE_PAUSE`` failures in a row the source pauses for a while
  (1, 2, 4, 8, then 15 minutes) and is tried again afterwards.  Every file that
  could not ask it in the meantime is deferred to the next run — never sent to
  Брак because of a network problem.
"""
from __future__ import annotations

import threading
import time


class Availability:
    FAILS_BEFORE_PAUSE = 3
    REJECTS_BEFORE_DISABLE = 3
    PAUSE_STEPS = (60, 120, 240, 480, 900)

    def _init_availability(self, log) -> None:
        self._availability_log = log
        self.disabled_reason = ""
        self.ever_disabled = ""          # the first reason it was switched off in this run (it may be back on)
        self.again_note = ""             # «Проверить снова»: "" — not asked; what the last asking said
        self.paused_until = 0.0          # time.monotonic() value
        self.pause_reason = ""
        self._asked_pause = False        # the pause is one its answer asked for (``pause_for``), not network errors
        self._fails = 0
        self._rejects = 0
        self._pause_step = 0
        self._availability_lock = threading.Lock()

    def _title(self) -> str:
        return str(getattr(self, "label", "") or getattr(self, "name", "") or "источник")

    # ── state ───────────────────────────────────────────────────────────────
    def ready(self) -> bool:
        return not self.disabled_reason and time.monotonic() >= self.paused_until

    def paused(self) -> bool:
        return not self.disabled_reason and time.monotonic() < self.paused_until

    def pause_note(self) -> str:
        left = max(0.0, self.paused_until - time.monotonic())
        until = time.strftime("%H:%M", time.localtime(time.time() + left))
        why = "пауза" if self._asked_pause else "пауза после ошибок сети"
        return f"{self._title()}: {why} до {until} ({self.pause_reason})"

    def pause_for(self, seconds: float, reason: str) -> None:
        """(v5.44.25) A rest the source's own answer asks for (a limit of the address): asked again after it; the
        files that wanted it meanwhile are deferred, never Брак."""
        with self._availability_lock:
            if self.disabled_reason:
                return
            self.paused_until = max(self.paused_until, time.monotonic() + max(0.0, float(seconds)))
            self.pause_reason = reason
            self._asked_pause = True
        self._availability_log(f"  ⏸ {self._title()}: {reason}")

    # ── transitions ─────────────────────────────────────────────────────────
    def disable(self, reason: str) -> None:
        """Permanent for this run (bad key, Cloudflare, API rejects us)."""
        with self._availability_lock:
            if self.disabled_reason:
                return
            self.disabled_reason = reason
            self.ever_disabled = self.ever_disabled or reason
        self._availability_log(f"  ⚠ {self._title()}: отключён до конца запуска — {reason}")

    def enable_again(self) -> None:
        """The person fixed what switched it off and it answered again: back on for the rest of the run."""
        with self._availability_lock:
            if not self.disabled_reason:
                return
            self.disabled_reason = ""
            self.paused_until = 0.0
            self._fails = 0
            self._rejects = 0
            self._pause_step = 0

    def note_success(self) -> None:
        with self._availability_lock:
            self._fails = 0
            self._rejects = 0
            self._pause_step = 0

    def note_failure(self, reason: str, *, transient: bool = True) -> None:
        message = ""
        disable_reason = ""
        with self._availability_lock:
            if transient:
                self._fails += 1
                if self._fails >= self.FAILS_BEFORE_PAUSE:
                    seconds = self.PAUSE_STEPS[min(self._pause_step, len(self.PAUSE_STEPS) - 1)]
                    self._pause_step += 1
                    self._fails = 0
                    self.paused_until = time.monotonic() + seconds
                    self.pause_reason = reason
                    self._asked_pause = False
                    message = (f"  ⏸ {self._title()}: {self.FAILS_BEFORE_PAUSE} ошибки сети подряд — "
                               f"пауза {seconds // 60} мин, потом попробую снова ({reason})")
            else:
                self._rejects += 1
                if self._rejects >= self.REJECTS_BEFORE_DISABLE:
                    disable_reason = f"{self.REJECTS_BEFORE_DISABLE} отказа подряд, последний: {reason}"
        if message:
            self._availability_log(message)
        if disable_reason:
            self.disable(disable_reason)
