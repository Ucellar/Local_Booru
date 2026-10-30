"""e621 internal IQDB (/iqdb_queries.json). Needs e621 login + API key."""
from __future__ import annotations

from core.parser.http import HttpFailure
from core.parser.models import Candidate, ReverseOutcome
from core.parser.reverse.base import ReverseService
from core.parser.util import json_body, looks_like_cloudflare, short


class E621Iqdb(ReverseService):
    name = "e621 IQDB"
    host = "e621.net"

    def _site(self):
        return next((s for s in self.sites if s.engine == "e621" and s.host == "e621.net"), None)

    def enabled(self) -> bool:
        site = self._site()
        return (self.ctx.flag("enable_e621_iqdb", True) and site is not None
                and bool(site.login and site.api_key) and not self.disabled_reason)

    def min_similarity(self) -> float:
        return self.ctx.setting_float("e621_iqdb_min_similarity", 80.0, 0.0, 100.0)

    def search(self, upload_path) -> ReverseOutcome:
        site = self._site()
        try:
            response = self.http.post_file(
                "https://e621.net/iqdb_queries.json", upload_path,
                headers={"Accept": "application/json", "User-Agent": site.api_user_agent()},
                auth=(site.login, site.api_key),
            )
        except HttpFailure as exc:
            return ReverseOutcome("failed", detail=exc.detail)
        status = response.status_code
        if looks_like_cloudflare(response):
            self.disable(f"Cloudflare (HTTP {status}) — открой e621 в браузере и обнови cookies через Companion")
            return ReverseOutcome("disabled", detail="Cloudflare")
        if status in (401, 403):
            self.disable(f"HTTP {status}: проверь логин и API-ключ e621")
            return ReverseOutcome("disabled", detail=f"HTTP {status}")
        if status == 429 or status >= 500:
            return ReverseOutcome("failed", detail=f"HTTP {status}")
        if status >= 400:
            return ReverseOutcome("bad_input", detail=f"HTTP {status}")
        data = json_body(response)
        if data is None:
            return ReverseOutcome("failed", detail="ответ не JSON: " + short(response.text, 80))
        rows = data if isinstance(data, list) else data.get("posts") or data.get("results") or []
        if not isinstance(rows, list):
            return ReverseOutcome("failed", detail="непонятный ответ: " + short(response.text, 80))
        outcome = ReverseOutcome("complete")
        min_sim = self.min_similarity()
        for row in rows:
            if not isinstance(row, dict):
                continue
            post_id = row.get("post_id") or (row.get("post") or {}).get("id")
            try:
                score = float(row.get("score", row.get("similarity", 0)) or 0)
            except (TypeError, ValueError):
                score = 0.0
            if str(post_id).isdigit() and score >= min_sim:
                outcome.candidates.append(Candidate(url=f"https://e621.net/posts/{post_id}", similarity=score,
                                                    service=self.name))
        for c in outcome.candidates[:5]:
            self.ctx.log(f"  e621 IQDB {c.similarity:.0f}% {c.url}")
        return outcome
