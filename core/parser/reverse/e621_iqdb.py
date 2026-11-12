"""e621's own search of similar pictures (/iqdb_queries.json).

(v5.44.14) It needs no account: e621 answers it without a login (checked 31.10.2026 on the owner's Брак — 96 of 204
pictures found, the owner's card 755 at 98 %).  Before, the service counted as switched off without the e621 login and
API key in «Сайты» — silently: the owner saw it «bring nothing» and turned it off.  The login and key are sent when
they are there.

Its pace.  e621's code (e621ng, ``IqdbQueriesController#throttle``) lets an address without an account one search
by file a minute and an account six in ten seconds.  So it was in a run of the parser (31.10.2026, one connection kept
open: answers 63–71 s apart, «Throttled» — HTTP 429 — between them); questions each on a connection of its own went
through a proxy of several addresses and were answered 3 s apart.  At the owner (1.11.2026, searches 3 s apart
without an account) e621's Cloudflare answered the twentieth one with its own HTTP 429 page.  So without an account the
searches go :data:`GAP` seconds apart — e621's minute — and with the login and key :data:`GAP_SIGNED` (v5.44.25: 6 s,
not 2 — at the owner's, with the account, Cloudflare answered 2 s apart with its 429); a «Throttled»
of e621 itself doubles the gap (up to :data:`MAX_GAP`), an answer takes it back a little; it is waited out —
``Retry-After``, else the gap after the last answer (at least :data:`THROTTLE_WAIT`, then :data:`THROTTLE_AGAIN`) —
and asked again, twice at most, before the file is deferred.  Cloudflare's 429 is a limit of the address for all of
e621: (v5.44.25) the first — :data:`CF_REST` seconds of rest (``pause_for``: the files meanwhile are deferred, not
Брак), then twice as far apart (at least :data:`CF_GAP`); the second — the service is not asked for the rest of the
run, so the sites' own lookups on e621 are not closed too (the words say so — not «refresh the cookies», which is for
its wall: 403/503).  A long queue holds no file: ``pass_over``
— the file goes on to the next service and comes back to it before the day's searches of SauceNAO
(``engine.reverse_next``).  With the e621 login and key in «Сайты» it is quick.

(v5.44.22) Its errors.  e621's staff wrote to the owner's account (14.06.2026): «you can stop spamming /iqdb_queries
with requests that will error out».  An error is not asked again file after file: HTTP 400 — e621's API help: «the
requested feature (such as an IQDB query) is not available» — and the answers that say the search itself is not taken
(:data:`REQUEST_UNFIT`) switch the service off for the rest of the run at once; an error about one search (500, another
4xx, an answer that is not one — e621's ``success: false``, not JSON) leaves that file without it for the run (it is
not remembered as a miss), and :data:`ERRORS_BEFORE_OFF` errors in a row switch it off; 502, 503 (e621's rate limit,
its API help), 504 and Cloudflare's 52x make the searches go further apart, the file waits, and the third one in a row
switches it off.  Only a picture e621 can take is sent (:data:`UPLOAD_TYPES`, at most :data:`UPLOAD_MAX` bytes): the
parser's copy of a file is a small JPEG, but a file it could not make one of goes as it is.
"""
from __future__ import annotations

import threading
import time

from core.parser.http import HttpFailure
from core.parser.models import Candidate, ReverseOutcome
from core.parser.reverse.base import ReverseService
from core.parser.util import json_body, looks_like_cloudflare, short

URL = "https://e621.net/iqdb_queries.json"
GAP = 61.0                # seconds between two searches without an account (e621: one a minute for an address)
GAP_SIGNED = 6.0          # … with the login and key (e621 itself: six in ten seconds for an account; its Cloudflare
                          # limits the address sooner — v5.44.25: at the owner's, 2 s apart with the account, HTTP 429)
CF_REST = 900.0           # seconds of rest after Cloudflare's 429 (the address's limit), then twice as far apart
CF_GAP = 12.0             # … at least this far apart after it
MAX_GAP = 240.0           # «Throttled» again and again (an address shared with others: a VPN)
THROTTLE_WAIT = 12.0      # seconds a «Throttled» answer is waited out when it does not say how long
THROTTLE_AGAIN = 30.0     # … and the second one in a row
ERRORS_BEFORE_OFF = 2     # errors in a row (no answer between them) that switch it off for the run
REQUEST_UNFIT = (400, 404, 405, 406, 410, 422, 501)  # the search itself is not taken: the same for every file
UPLOAD_TYPES = (".jpg", ".jpeg", ".png")
UPLOAD_MAX = 8 * 1024 * 1024


class E621Iqdb(ReverseService):
    name = "e621 IQDB"
    journal_key = "e621_iqdb"
    journal_revision = 2      # (v5.44.14) asked without an account: a «нет» of before (it never ran) does not count
    host = "e621.net"
    pass_over = True          # its queue long (throttled) — a file goes on and comes back (engine.reverse_next)
    # (v5.44.36) e621's previews of videos: ffmpeg's «thumbnail» over the first 100 frames — of 7 public e621 videos
    # it found 5 by that frame (3 at 80 % and more), 1 by the first frame
    video_styles = ("e621",)
    # (v5.44.36) hits from this score up under the run's threshold are kept as weak (``Candidate.weak``): a video's
    # only, with a nearly same preview — e621 IQDB scored true finds of videos 68–75 % (the owner's Брак: 68.8 %, the
    # preview its quarter frame, d=0; public videos: 72 %, 75 %, the preview the e621 frame)
    weak_floor = 65.0

    def __init__(self, ctx, sites: list):
        super().__init__(ctx, sites)
        self._turn = threading.Lock()         # one search at a time, gap() apart
        self._last = 0.0
        self._gap = 0.0                       # learned; 0 — not yet: the account's own
        self._last_ok = 0.0                   # when e621 last answered (its minute counts from it)
        self._errors = 0                      # error answers in a row
        self._limited = 0                     # Cloudflare's 429 in this run

    def _site(self):
        return next((s for s in self.sites if s.engine == "e621" and s.host == "e621.net"), None)

    def _signed(self, site=None) -> bool:
        site = site or self._site()
        return bool(site is not None and site.login and site.api_key)

    def enabled(self) -> bool:
        """Switched on and e621 is one of the run's sites (its posts are where the hits lead); no account needed."""
        return self.ctx.flag("enable_e621_iqdb", True) and self._site() is not None and not self.disabled_reason

    def min_similarity(self) -> float:
        """«Порог IQDB» of «Сайты и ключи» (it says «IQDB и e621 IQDB»); a hit under ``trust_similarity`` is compared
        with the file's picture anyway."""
        return self.ctx.setting_float("iqdb_min_similarity", 80.0, 0.0, 100.0)

    def gap(self) -> float:
        """Seconds between two searches now: the account's own, longer after «Throttled»."""
        base = GAP_SIGNED if self._signed() else GAP
        return max(base, self._gap)

    def per_minute(self) -> float:
        host = super().per_minute()
        mine = 60.0 / self.gap()
        return min(host, mine) if host else mine

    def _throttled(self) -> None:
        self._gap = min(MAX_GAP, self.gap() * 2.0)

    def _answered(self) -> None:
        base = GAP_SIGNED if self._signed() else GAP
        self._gap = max(base, self.gap() * 0.9)
        self._last_ok = time.monotonic()
        self._errors = 0

    def _error(self, status: int, *, unfit: bool = False, what: str = "") -> ReverseOutcome:
        """An answer that is an error (v5.44.22): the file goes on without e621 IQDB (it is not asked about it again
        in this run); the search does not fit (``unfit``) or :data:`ERRORS_BEFORE_OFF` errors in a row — the service
        is not asked for the rest of the run."""
        self._errors += 1
        what = what or f"HTTP {status}"
        if status == 400:
            why = "e621 сейчас не принимает поиск по картинке (HTTP 400 — «функция недоступна»)"
        elif unfit:
            why = f"e621 не принимает такой поиск по картинке ({what})"
        else:
            why = f"e621 ответил ошибкой на поиск по картинке {self._errors} раза подряд (последний раз — {what})"
        if unfit or self._errors >= ERRORS_BEFORE_OFF:
            self.disable(why + " — до конца запуска не спрашиваю: e621 просит не слать запросы, которые падают")
            return ReverseOutcome("disabled", detail=what)
        return ReverseOutcome("bad_input", detail=f"{what} — этот файл на e621 IQDB больше не ищу")

    def _unreached(self, status: int) -> ReverseOutcome:
        """502, 503 (e621's rate limit, its API help), 504, 52x (Cloudflare did not reach e621): the file waits for it
        (the next search goes later); more than :data:`ERRORS_BEFORE_OFF` errors in a row — off for the run."""
        self._errors += 1
        self._throttled()
        if self._errors > ERRORS_BEFORE_OFF:
            self.disable(f"e621 не отвечает на поиск по картинке ({self._errors} раза подряд, последний раз — HTTP "
                         f"{status}) — до конца запуска не спрашиваю")
            return ReverseOutcome("disabled", detail=f"HTTP {status}")
        return ReverseOutcome("failed", detail=f"HTTP {status}" + (" — e621 просит реже" if status == 503 else ""))

    def search(self, upload_path, *, again: int = 0) -> ReverseOutcome:
        site = self._site()
        if site is None:
            return ReverseOutcome("disabled", detail="e621 нет среди сайтов запуска")
        unfit = self._unfit(upload_path)
        if unfit:
            return ReverseOutcome("bad_input", detail=unfit)
        signed = self._signed(site)
        try:
            with self._turn:
                left = self._last + self.gap() - time.monotonic()
                if left > 0:
                    self.http.sleep(left)
                try:
                    response = self.http.post_file(
                        URL, upload_path,
                        headers={"Accept": "application/json", "User-Agent": site.api_user_agent()},
                        auth=(site.login, site.api_key) if signed else None,
                    )
                finally:
                    self._last = time.monotonic()
        except HttpFailure as exc:
            return ReverseOutcome("failed", detail=exc.detail)
        status = response.status_code
        if looks_like_cloudflare(response):
            if status == 429:       # a limit of the address, not a wall: cookies do not lift it, asking more widens it
                self._limited += 1
                if self._limited == 1:              # (v5.44.25) a rest, then twice as far apart — not the run lost
                    self._gap = min(MAX_GAP, max(self.gap() * 2.0, CF_GAP))
                    self.pause_for(CF_REST, f"e621 ограничил частые поиски по картинке с этого адреса (HTTP 429) — "
                                            f"{int(CF_REST // 60)} минут не спрашиваю, потом раз в {self.gap():.0f} с")
                    return ReverseOutcome("paused", detail="HTTP 429 — e621 просит реже")
                self.disable("e621 второй раз ограничил частые поиски по картинке с этого адреса (HTTP 429) — до конца "
                             "запуска не спрашиваю, чтобы e621 не закрыл и остальное" +
                             ("" if signed else "; с логином и API-ключом e621 («Настройки → Парсер и сайты») "
                                                "поисков больше"))
                return ReverseOutcome("disabled", detail="HTTP 429")
            self.disable(f"Cloudflare (HTTP {status}) — открой e621 в браузере и обнови cookies через Companion")
            return ReverseOutcome("disabled", detail="Cloudflare")
        if status in (401, 403):
            self.disable(f"HTTP {status}: проверь логин и API-ключ e621" if signed
                         else f"HTTP {status}: e621 просит логин и API-ключ («Настройки → Парсер и сайты»)")
            return ReverseOutcome("disabled", detail=f"HTTP {status}")
        if status == 429:
            self._throttled()
            if again < 2:
                try:
                    wait = float(response.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                if wait <= 0:                 # e621 does not say: the gap from the last answer
                    wait = max(THROTTLE_WAIT if not again else THROTTLE_AGAIN,
                               self._last_ok + self.gap() - time.monotonic() if self._last_ok else 0.0)
                self.http.sleep(max(1.0, min(wait, MAX_GAP)))
                return self.search(upload_path, again=again + 1)
            slow = " — e621 без логина ищет медленно; логин и ключ — «Настройки → Парсер и сайты»"
            return ReverseOutcome("failed", detail=f"HTTP {status}" + ("" if signed else slow))
        if status in REQUEST_UNFIT:             # 400 — «the requested feature (such as an IQDB query) is not available»
            return self._error(status, unfit=True)
        if 400 <= status <= 500:                # an error about this one search
            return self._error(status)
        if status > 500:
            return self._unreached(status)
        data = json_body(response)
        if data is None:
            return self._error(status, what="ответ не JSON: " + short(response.text, 60))
        if isinstance(data, dict) and data.get("success") is False:     # e621's answer of an error, sent as 200
            return self._error(status, what="e621: " + short(data.get("message") or data.get("reason") or "ошибка", 60))
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
            if str(post_id).isdigit() and score >= min(min_sim, self.weak_floor):
                outcome.candidates.append(Candidate(url=f"https://e621.net/posts/{post_id}", similarity=score,
                                                    service=self.name, weak=score < min_sim))
        for c in outcome.candidates[:5]:
            self.ctx.log(f"  e621 IQDB {c.similarity:.0f}% {c.url}")
        return outcome

    @staticmethod
    def _unfit(upload_path) -> str:
        """Why the picture is not sent to e621 ("" — it is sent): not a JPEG or PNG, or too big."""
        from pathlib import Path
        path = Path(upload_path)
        if path.suffix.lower() not in UPLOAD_TYPES:
            return f"{path.suffix or 'без расширения'} — e621 IQDB ищет по JPEG и PNG, не спрашиваю"
        try:
            size = path.stat().st_size
        except OSError:
            return "картинки для поиска нет"
        if size > UPLOAD_MAX:
            return f"картинка {size // (1024 * 1024)} МБ — для e621 IQDB больше {UPLOAD_MAX // (1024 * 1024)} МБ не шлю"
        return ""
