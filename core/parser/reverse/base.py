"""Common base for reverse image search services.

ReverseOutcome.status values returned by search():
    complete  — the service answered (candidates may be empty);
    deferred  — quota/limit; retry_after says when (the file is deferred);
    failed    — temporary trouble: network, HTTP 429/5xx, a page that is not a result
                (the file is deferred; three in a row pause the service for a while);
    bad_input — this particular file cannot be searched (too large, unreadable);
    disabled  — the service switched itself off (bad key, Cloudflare); see Availability.
"""
from __future__ import annotations

from core.parser.availability import Availability
from core.parser.context import ParserContext
from core.parser.models import ReverseOutcome


class ReverseService(Availability):
    name = "reverse"
    #: similarity at which a hit is accepted without a visual check
    trust_similarity = 93.0
    #: (v5.44.14) a hit under it needs a preview nearly the file's (``engine._accept`` ``strict``): 0 — never
    strict_below = 0.0
    #: the host its requests go to: its pace is that host's (core/http_rate_limiter.py)
    host = ""
    #: (v5.44.18) its name in the journal of answers (``reverse_branch_status``, the old program's ``branch_key``:
    #: «iqdb», «saucenao»…) and the revision of its answers: a row of an older one is asked again
    #: (core/parser/reverse_journal.py)
    journal_key = ""
    journal_revision = 1

    def __init__(self, ctx: ParserContext, sites: list):
        self.ctx = ctx
        self.sites = sites
        self.http = ctx.http
        self._init_availability(ctx.log)

    def enabled(self) -> bool:
        """Switched on and configured by the user (pauses are checked separately)."""
        return False

    def min_similarity(self) -> float:
        return self.ctx.setting_float("min_similarity", 85.0, 0.0, 100.0)

    def search(self, upload_path) -> ReverseOutcome:
        raise NotImplementedError

    def per_minute(self) -> float:
        """Searches a minute its host's limits allow at most (0 — no limit known)."""
        from core.http_rate_limiter import delay_for, requests_per_minute_for
        if not self.host:
            return 0.0
        settings = getattr(self.http, "_limiter_settings", None) or self.ctx.settings
        rpm = requests_per_minute_for(self.host, settings)
        delay = delay_for(self.host, settings)
        paces = [float(x) for x in (rpm, 60.0 / delay if delay > 0 else None) if x]
        return min(paces) if paces else 0.0

    def expected_per_minute(self) -> float:
        """Searches a minute to expect before the queue has a pace of its own (``runner.lane_wait``)."""
        return self.per_minute()

    def pace_note(self) -> str:
        """How the service is asked, for the page of the run: one file at a time, at most so many a minute."""
        pace = self.per_minute()
        return f"по одному, до {pace:.0f} в минуту" if pace >= 1 else "по одному"
