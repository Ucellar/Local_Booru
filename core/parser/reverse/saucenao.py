"""SauceNAO API (needs an API key). Quota-aware: never treats a limit as a miss.

Every answer says the account's limits: searches per 30 seconds (``short_limit``) and per day (``long_limit``,
``long_remaining``).  The pace follows the 30-second one (one search fewer, so another program's search does not
make it overflow — ``learn_pace``); the daily one is kept by the quota governor (core/services/saucenao_quota.py:
a reserve stays, the rest spread over «Растянуть суточный лимит SauceNAO»).  HTTP 429: the daily limit — a long
pause, the file deferred; the 30-second one — its window waited out and the file asked once more."""
from __future__ import annotations

import re
import time

from core.parser.http import HttpFailure
from core.parser.models import Candidate, ReverseOutcome
from core.parser.reverse.base import ReverseService
from core.parser.util import host_of, json_body, looks_like_cloudflare, short

API = "https://saucenao.com/search.php"

_ID_URLS = {
    "danbooru_id": "https://danbooru.donmai.us/posts/{}",
    "gelbooru_id": "https://gelbooru.com/index.php?page=post&s=view&id={}",
    "yandere_id": "https://yande.re/post/show/{}",
    "konachan_id": "https://konachan.com/post/show/{}",
    "e621_id": "https://e621.net/posts/{}",
}

_AUTH_WORDS = ("api key", "apikey", "invalid key", "unauthorized", "authentication", "forbidden")
_QUOTA_WORDS = ("limit exceeded", "search limit", "out of searches", "no searches", "quota", "daily limit")
_DAILY_WORDS = ("daily", "24 hour", "long limit", "long_limit")
SHORT_WINDOW = 30.0          # seconds of SauceNAO's short limit
SHORT_WAIT = SHORT_WINDOW + 2.0   # how long a full 30-second window is waited out before the file is asked again


class SauceNao(ReverseService):
    name = "SauceNAO"
    journal_key = "saucenao"
    host = "saucenao.com"
    quota_limited = True          # (v5.44) searches a day: a file passes it over while its queue is long (engine)
    # (v5.44.36) its Gelbooru found the owner's videos by their start (93–95 %); its Danbooru knows them by the frame
    # Danbooru makes its previews of — asked with it when the start found nothing
    video_styles = ("start", "danbooru")

    def __init__(self, ctx, sites: list):
        super().__init__(ctx, sites)
        self.limits: dict = {}            # what the last answer said: short_limit, long_limit, long_remaining

    def enabled(self) -> bool:
        return self.ctx.flag("enable_saucenao", True) and bool(self._key()) and not self.disabled_reason

    def expected_per_minute(self) -> float:
        """Searches a minute: the account's 30-second limit, or the day's searches left spread as set (the quota
        governor), whichever is slower."""
        pace = self.per_minute()
        left = self.limits.get("long_remaining")
        try:
            from core.services.saucenao_quota import enabled as governed, reserve_for, spread_seconds_for
            if governed(self.ctx.settings) and isinstance(left, int) and left > reserve_for(self.ctx.settings):
                spread = 60.0 * (left - reserve_for(self.ctx.settings)) / spread_seconds_for(self.ctx.settings)
                pace = min(pace, spread) if pace else spread
        except ImportError:
            pass
        return pace

    def pace_note(self) -> str:
        """The pace on the page of the run: the account's limit, and what is left of the day's searches."""
        pace = self.expected_per_minute()
        left = self.limits.get("long_remaining")
        text = f"по одному, до {pace:.0f} в минуту" if pace >= 1 else (
            f"по одному, раз в {60.0 / pace:.0f} с" if pace > 0 else "по одному")
        return text + (f" · на сутки осталось {left}" if isinstance(left, int) and left >= 0 else "")

    def _key(self) -> str:
        return str(self.ctx.settings.get("saucenao_api_key") or "").strip()

    # ── cooldown shared with the UI status line ─────────────────────────────
    def cooldown_until(self) -> int:
        return self._pause()[0]

    def _pause(self) -> tuple[int, str]:
        """(until when SauceNAO is not asked, why — as ``_set_cooldown`` wrote it: «HTTP 521», «лимит исчерпан»…)."""
        try:
            from core.services.service_state import get_cooldown
            state = get_cooldown(self.ctx.settings, "saucenao")
            return int(float(state.get("cooldown_until", 0) or 0)), str(state.get("reason") or "")
        except Exception as exc:
            self.ctx.log(f"  SauceNAO: состояние лимита не прочитано: {exc}")
            return 0, ""

    def _set_cooldown(self, reason: str, seconds: int | None = None) -> int:
        if seconds is None:
            seconds = self.ctx.setting_int("saucenao_cooldown_seconds", 3600, 60, 86400)
        until = int(time.time()) + max(60, int(seconds))
        try:
            from core.services.service_state import set_cooldown
            set_cooldown(self.ctx.settings, "saucenao", until, reason=reason)
        except Exception as exc:
            self.ctx.log(f"  SauceNAO: не удалось сохранить паузу: {exc}")
        self.ctx.log(f"  SAUCENAO COOLDOWN: пауза {max(1, (until - int(time.time())) // 60)} мин ({reason})")
        return until

    # ── search ──────────────────────────────────────────────────────────────
    def search(self, upload_path, *, again: bool = False) -> ReverseOutcome:
        until, why = self._pause()
        if until > time.time():       # the pause says why: a limit, or the server's failure (HTTP 521 — not a limit)
            return ReverseOutcome("deferred", retry_after=until,
                                  detail=f"пауза ({why})" if why else "пауза после лимита")
        from core.http_rate_limiter import SauceNAOQuotaDeferred, take_saucenao_admission_serial
        params = {
            "api_key": self._key(), "output_type": 2, "numres": 6,
            "dedupe": self.ctx.setting_int("saucenao_dedupe", 1, 0, 2),
            "hide": self.ctx.setting_int("saucenao_hide", 0, 0, 3),
        }
        dbs = [int(x) for x in self.ctx.settings.get("saucenao_dbs") or [] if str(x).isdigit()]
        if dbs:
            params["dbs[]"] = dbs + self._manga_indexes(dbs)
        elif str(self.ctx.settings.get("saucenao_dbmask") or "").strip():
            params["dbmask"] = str(self.ctx.settings.get("saucenao_dbmask")).strip()
        else:
            params["db"] = self.ctx.setting_int("saucenao_db", 999, 0, 999)
        serial = 0
        try:
            response = self.http.post_file(API, upload_path, params=params)
        except SauceNAOQuotaDeferred as exc:
            return ReverseOutcome("deferred", retry_after=exc.retry_after, detail=f"бережём суточный лимит ({exc.reason})")
        except HttpFailure as exc:
            return ReverseOutcome("failed", detail=exc.detail)
        finally:
            serial = take_saucenao_admission_serial()
        if response.status_code == 429:
            return self._limited(response, upload_path, serial, again)
        if response.status_code >= 500:
            return ReverseOutcome("deferred", retry_after=self._set_cooldown(f"HTTP {response.status_code}", 300),
                                  detail=f"HTTP {response.status_code}")
        if response.status_code >= 400:
            if response.status_code in (401, 403):
                self.disable(f"HTTP {response.status_code}: проверь API-ключ")
                return ReverseOutcome("disabled", detail=f"HTTP {response.status_code}")
            # 413 (file too large) and other client errors belong to this file only
            return ReverseOutcome("bad_input", detail=f"HTTP {response.status_code}")
        data = json_body(response)
        if not isinstance(data, dict) or not isinstance(data.get("header"), dict):
            if looks_like_cloudflare(response):
                self.disable("Cloudflare — открой saucenao.com в браузере")
                return ReverseOutcome("disabled", detail="Cloudflare")
            return ReverseOutcome("failed", detail="ответ не JSON: " + short(response.text, 80))
        header = data["header"]
        self._record_quota(header, serial)
        try:
            api_status = int(header.get("status"))
        except (TypeError, ValueError):
            return ReverseOutcome("failed", detail="нет header.status")
        message = str(header.get("message") or data.get("message") or "")
        low = message.lower()
        if api_status > 0:
            # SauceNAO's own example: a positive status — «one or more indexes are having an issue»; the search is
            # counted, waiting is its advice.  (v5.44.35) Without words of its own the reason is said (was empty: «SauceNAO:
            # отложено — » in the owner's journal of 2.11.2026)
            return ReverseOutcome("deferred", retry_after=self._set_cooldown(f"сбой сервера {api_status}", 300),
                                  detail=short(message) or f"сбой сервера {api_status}")
        if api_status < 0:
            if any(w in low for w in _AUTH_WORDS):
                self.disable("неверный API-ключ")
                return ReverseOutcome("disabled", detail=short(message))
            if any(w in low for w in _QUOTA_WORDS):
                return ReverseOutcome("deferred", retry_after=self._set_cooldown("лимит исчерпан"), detail=short(message))
            # Negative statuses are client-side errors about this upload (SauceNAO API docs):
            # retrying the same file later would fail the same way, so never defer on them.
            return ReverseOutcome("bad_input", detail=short(message))
        try:
            api_min = float(header.get("minimum_similarity") or 0)
        except (TypeError, ValueError):
            api_min = 0.0
        min_sim = max(self.min_similarity(), api_min)
        outcome = ReverseOutcome("complete")
        for result in data.get("results") or []:
            self._collect(result, min_sim, outcome)
        outcome.manga = self._manga(data)
        return outcome

    def _manga_on(self) -> bool:
        """(v5.44.164) «Проверять папки и класть мангу в «Мангу»» of the page «Парсер»: a picture is asked whether it
        is a page of a manga too."""
        return bool(self.ctx.settings.get("parser_skip_manga", True))

    def _manga_indexes(self, dbs: list) -> list:
        """(v5.44.164) SauceNAO's indexes of manga, added to the ones the person chose («saucenao_dbs»): the same one
        search then says whether the picture is a page of a manga.  Every index asked (the default) has them already."""
        if not self._manga_on():
            return []
        from core.manga_pictures import INDEXES
        return [index for index in INDEXES if index not in dbs]

    def _manga(self, data: dict) -> list:
        """(v5.44.164) The manga the answer names (``manga_pictures.manga_hits``); said in the journal with the other
        answers (:meth:`_collect`), kept for the file's end (``engine._finish_miss`` → ``core/parser_loose.py``)."""
        if not self._manga_on():
            return []
        try:
            from core.manga_pictures import manga_hits
            return manga_hits(data)
        except Exception as exc:  # noqa: BLE001 — the file's own search is done all the same
            self.ctx.log(f"  SauceNAO: ответ о манге не разобран: {type(exc).__name__}")
            return []

    def _limited(self, response, upload_path, serial: int, again: bool) -> ReverseOutcome:
        """HTTP 429.  The daily limit: a long pause (the file deferred to the next run).  The 30-second one (another
        program searched meanwhile, or the account's pace was not known yet): its window waited out and the file asked
        once more; twice in a row — a short pause."""
        data = json_body(response)
        header = data.get("header") if isinstance(data, dict) and isinstance(data.get("header"), dict) else {}
        if header:
            self._record_quota(header, serial)
        text = str(header.get("message") or response.text or "").lower()
        daily = any(word in text for word in _DAILY_WORDS)
        try:
            daily = daily or int(header.get("long_remaining")) <= 0
        except (TypeError, ValueError):
            pass
        if daily:
            until = self.cooldown_until()          # the answer's own counter may have set the pause already
            if until <= time.time():
                until = self._set_cooldown("суточный лимит (HTTP 429)")
            return ReverseOutcome("deferred", retry_after=until, detail="суточный лимит")
        if not again:
            self.ctx.log("  SauceNAO: лимит на 30 секунд — жду полминуты и спрашиваю снова")
            self.http.sleep(SHORT_WAIT)
            return self.search(upload_path, again=True)
        return ReverseOutcome("deferred", retry_after=self._set_cooldown("HTTP 429 дважды подряд", 120),
                              detail="HTTP 429")

    def _learn(self, header: dict) -> None:
        """The account's limits from an answer: its pace follows the 30-second one (one search fewer)."""
        from core.http_rate_limiter import learn_pace
        limits = dict(self.limits)
        for key in ("short_limit", "long_limit", "long_remaining", "short_remaining"):
            try:
                limits[key] = int(header.get(key))
            except (TypeError, ValueError):
                continue
        self.limits = limits
        short = limits.get("short_limit")
        if isinstance(short, int) and short >= 2:
            room = short - 1
            learn_pace(self.host, interval=SHORT_WINDOW / room, rpm=int(room * 60 / SHORT_WINDOW))

    def _record_quota(self, header: dict, serial: int) -> None:
        self._learn(header)
        short_left, long_left = header.get("short_remaining"), header.get("long_remaining")
        if short_left is None and long_left is None:
            return
        try:
            from core.services.service_state import set_quota_snapshot
            set_quota_snapshot(
                self.ctx.settings, "saucenao", short_remaining=short_left, long_remaining=long_left,
                probe_interval_seconds=self.ctx.settings.get("saucenao_quota_probe_interval_seconds", 3600),
                reserve=self.ctx.settings.get("saucenao_quota_reserve", 10), admission_serial=serial,
            )
        except Exception as exc:
            self.ctx.log(f"  SauceNAO: квота не сохранена: {exc}")
        self.ctx.log(f"  SAUCENAO LIMITS: 30с={short_left} сутки={long_left}")
        try:
            if int(long_left) <= 0:
                self._set_cooldown("суточный лимит исчерпан")
        except (TypeError, ValueError):
            pass

    def _collect(self, result: dict, min_sim: float, outcome: ReverseOutcome) -> None:
        head = result.get("header") or {}
        data = result.get("data") or {}
        try:
            sim = float(head.get("similarity") or 0)
        except (TypeError, ValueError):
            return
        title = str(data.get("title") or data.get("source") or head.get("index_name") or "")
        if sim < min_sim:
            return
        urls = []
        for key, template in _ID_URLS.items():
            if str(data.get(key) or "").isdigit():
                urls.append(template.format(data[key]))
        for url in data.get("ext_urls") or []:
            if isinstance(url, str) and url not in urls:
                urls.append(url)
        pixiv_id = str(data.get("pixiv_id") or "")
        if not pixiv_id:
            for url in urls:
                m = re.search(r"pixiv\.net/(?:en/)?artworks/(\d+)|illust_id=(\d+)", url)
                if m:
                    pixiv_id = m.group(1) or m.group(2)
                    break
        supported = False
        for url in urls:
            if any(site.handles(host_of(url)) for site in self.sites):
                outcome.candidates.append(Candidate(url=url, similarity=sim, service=self.name, title=title))
                supported = True
        if pixiv_id:
            outcome.candidates.append(Candidate(url=f"https://www.pixiv.net/artworks/{pixiv_id}", similarity=sim,
                                                service=self.name, title=title, pixiv_id=pixiv_id))
            supported = True
        if not supported:
            source = data.get("source") if isinstance(data.get("source"), str) else ""
            best = next((u for u in urls if u.startswith("http")), source if source.startswith("http") else "")
            outcome.source_only.append({"url": best, "label": f"SauceNAO {head.get('index_name', '')}".strip(),
                                        "similarity": sim})
        self.ctx.log(f"  SauceNAO {sim:.1f}% {head.get('index_name', '')} {short(title, 60)}")
