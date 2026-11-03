"""All The Fallen's own search of similar pictures (booru.allthefallen.moe/iqdb_queries.json).

(v5.44.39) The owner (2.11.2026): «в обратный поиск добавить atf/iqdb_queries».  ATF runs Danbooru's software, so it
has the same image search e621 does — the picture is uploaded to ``/iqdb_queries.json`` and the site answers with its
own posts and a similarity score.  It needs the ATF login and API key of «Сайты и ключи» (ATF shows nothing without an
account), and the upload goes through the same verification page (proof-of-work) as every other ATF request — the site
layer solves it once and keeps the cookie ten minutes (``core/parser/sites/danbooru.py``: ``iqdb_query``), so this
service does not touch that gate, it reuses it.

Its pace.  ATF is a small site; the searches go :data:`GAP` seconds apart, a «просит реже» (HTTP 429/503) doubles the
gap up to :data:`MAX_GAP` and the file waits, an answer brings it back.  Errors: HTTP 400/404/501 — the site does not
offer image search (its IQDB is off) — switch the service off for the run; other 4xx — the file goes on without it;
:data:`ERRORS_BEFORE_OFF` errors or «не отвечает» in a row — off for the run.  Only a JPEG or PNG up to
:data:`UPLOAD_MAX` bytes is sent (the parser's copy of a file is a small JPEG).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from core.parser.http import HttpFailure
from core.parser.models import Candidate, ReverseOutcome
from core.parser.reverse.base import ReverseService
from core.parser.sites.base import SiteUnavailable
from core.parser.util import json_body, looks_like_cloudflare, short

GAP = 5.0                 # seconds between two searches (ATF is a small site — gently)
MAX_GAP = 180.0           # «просит реже» again and again
THROTTLE_WAIT = 15.0      # seconds a 429/503 without «Retry-After» is waited out
ERRORS_BEFORE_OFF = 2     # error answers in a row (no answer between them) that switch it off for the run
REQUEST_UNFIT = (400, 404, 405, 406, 410, 501)  # the site does not offer image search: the same for every file
UPLOAD_TYPES = (".jpg", ".jpeg", ".png")
UPLOAD_MAX = 8 * 1024 * 1024


class AtfIqdb(ReverseService):
    name = "ATF IQDB"
    journal_key = "atf_iqdb"
    journal_revision = 1
    host = "booru.allthefallen.moe"
    pass_over = True          # its queue can be slow — a file goes on and comes back (engine.reverse_next)
    # (v5.44.39) ATF is Danbooru's software: a video's preview is the frame of ffmpeg's «thumbnail» over the first 300
    # frames, as Danbooru and iqdb.org make it (core/tagger/site_frames.py)
    video_styles = ("danbooru",)

    def __init__(self, ctx, sites: list):
        super().__init__(ctx, sites)
        self._turn = threading.Lock()         # one search at a time, gap() apart
        self._last = 0.0
        self._gap = 0.0                       # learned; grows after «просит реже»
        self._errors = 0                      # error answers in a row

    def _site(self):
        return next((s for s in self.sites if s.engine == "danbooru" and getattr(s, "is_atf", False)), None)

    def enabled(self) -> bool:
        """Switched on, ATF is one of the run's sites (its posts are where the hits lead) and has its login and API key.
        (v5.44.46) Without them it is not asked at all: ATF shows an anonymous search nothing, so an empty answer would
        be written as «не найдено» (``reverse_journal``) and the file never asked again once the key is there —
        like SauceNAO without its key («Сайты и ключи» says ATF IQDB needs them)."""
        site = self._site()
        return (self.ctx.flag("enable_atf_iqdb", True) and site is not None and bool(site.login and site.api_key)
                and not self.disabled_reason)

    def min_similarity(self) -> float:
        """«Порог IQDB» of «Сайты и ключи» (it says «IQDB и e621 IQDB»)."""
        return self.ctx.setting_float("iqdb_min_similarity", 80.0, 0.0, 100.0)

    def gap(self) -> float:
        return max(GAP, self._gap)

    def per_minute(self) -> float:
        host = super().per_minute()
        mine = 60.0 / self.gap()
        return min(host, mine) if host else mine

    def _throttled(self) -> None:
        self._gap = min(MAX_GAP, max(self.gap() * 2.0, GAP * 2.0))

    def _answered(self) -> None:
        self._gap = max(GAP, self.gap() * 0.9)
        self._errors = 0

    def _error(self, status: int, *, unfit: bool = False, what: str = "") -> ReverseOutcome:
        self._errors += 1
        what = what or f"HTTP {status}"
        if unfit:
            self.disable(f"ATF не принимает поиск по картинке ({what}) — у этого ATF поиск похожих, похоже, выключен; "
                         "до конца запуска не спрашиваю")
            return ReverseOutcome("disabled", detail=what)
        if self._errors >= ERRORS_BEFORE_OFF:
            self.disable(f"ATF ответил ошибкой на поиск по картинке {self._errors} раза подряд (последний раз — "
                         f"{what}) — до конца запуска не спрашиваю")
            return ReverseOutcome("disabled", detail=what)
        return ReverseOutcome("bad_input", detail=f"{what} — этот файл на ATF IQDB больше не ищу")

    def _unreached(self, status: int) -> ReverseOutcome:
        self._errors += 1
        self._throttled()
        if self._errors > ERRORS_BEFORE_OFF:
            self.disable(f"ATF не отвечает на поиск по картинке ({self._errors} раза подряд, последний раз — HTTP "
                         f"{status}) — до конца запуска не спрашиваю")
            return ReverseOutcome("disabled", detail=f"HTTP {status}")
        return ReverseOutcome("failed", detail=f"HTTP {status}")

    def search(self, upload_path, *, again: int = 0) -> ReverseOutcome:
        site = self._site()
        if site is None:
            return ReverseOutcome("disabled", detail="ATF нет среди сайтов запуска")
        unfit = self._unfit(upload_path)
        if unfit:
            return ReverseOutcome("bad_input", detail=unfit)
        try:
            with self._turn:
                left = self._last + self.gap() - time.monotonic()
                if left > 0:
                    self.http.sleep(left)
                try:
                    response = site.iqdb_query(upload_path)
                finally:
                    self._last = time.monotonic()
        except HttpFailure as exc:
            return ReverseOutcome("failed", detail=exc.detail)
        except SiteUnavailable as exc:        # the verification page could not be passed (danbooru.iqdb_query)
            self.disable(f"не удалось пройти проверку ATF для поиска по картинке ({short(str(exc), 60)}) — до конца "
                         "запуска не спрашиваю")
            return ReverseOutcome("disabled", detail="проверка ATF")
        status = response.status_code
        if looks_like_cloudflare(response):
            self.disable(f"Cloudflare (HTTP {status}) на ATF — открой ATF в браузере и обнови cookies через Companion")
            return ReverseOutcome("disabled", detail="Cloudflare")
        if status in (401, 403):
            self.disable(f"HTTP {status}: проверь логин и API-ключ ATF в «Сайтах и ключах»")
            return ReverseOutcome("disabled", detail=f"HTTP {status}")
        if status in (429, 503):
            self._throttled()
            if again < 2:
                try:
                    wait = float(response.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                self.http.sleep(max(1.0, min(wait or THROTTLE_WAIT, MAX_GAP)))
                return self.search(upload_path, again=again + 1)
            return ReverseOutcome("failed", detail=f"HTTP {status} — ATF просит реже")
        if status in REQUEST_UNFIT:
            return self._error(status, unfit=True)
        if 400 <= status < 500:
            return self._error(status)
        if status >= 500:
            return self._unreached(status)
        data = json_body(response)
        if data is None:
            return self._error(status, what="ответ не JSON: " + short(response.text, 60))
        rows = data if isinstance(data, list) else data.get("posts") or data.get("results") or []
        if not isinstance(rows, list):
            return self._error(status, what="непонятный ответ: " + short(response.text, 60))
        self._answered()
        outcome = ReverseOutcome("complete")
        min_sim = self.min_similarity()
        for row in rows:
            if not isinstance(row, dict):
                continue
            post = row.get("post") if isinstance(row.get("post"), dict) else {}
            inner = post.get("posts") if isinstance(post.get("posts"), dict) else post
            post_id = row.get("post_id") or inner.get("id")
            try:
                score = float(row.get("score", row.get("similarity", 0)) or 0)
            except (TypeError, ValueError):
                score = 0.0
            if str(post_id).isdigit() and score >= min_sim:
                outcome.candidates.append(Candidate(url=site.post_url(str(post_id)), similarity=score,
                                                     service=self.name))
        for c in outcome.candidates[:5]:
            self.ctx.log(f"  ATF IQDB {c.similarity:.0f}% {c.url}")
        return outcome

    @staticmethod
    def _unfit(upload_path) -> str:
        """Why the picture is not sent ("" — it is sent): not a JPEG or PNG, or too big."""
        path = Path(upload_path)
        if path.suffix.lower() not in UPLOAD_TYPES:
            return f"{path.suffix or 'без расширения'} — ATF IQDB ищет по JPEG и PNG, не спрашиваю"
        try:
            size = path.stat().st_size
        except OSError:
            return "картинки для поиска нет"
        if size > UPLOAD_MAX:
            return f"картинка {size // (1024 * 1024)} МБ — для ATF IQDB больше {UPLOAD_MAX // (1024 * 1024)} МБ не шлю"
        return ""
