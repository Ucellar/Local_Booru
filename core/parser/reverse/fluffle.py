"""Fluffle (fluffle.xyz): the search of similar pictures over e621, FurAffinity, Twitter, Bluesky, Inkbunny, Weasyl,
DeviantArt and Furry Network (v5.44.14, the owner: «любые способы уменьшения брака»).  Free, no account; checked
31.10.2026 on the owner's Брак: 62 of 120 pictures found («exact» or «tossUp»), 15 of them not found by e621's own
search.

An e621 post it names is a hit like any other (``https://e621.net/posts/<n>``: the site's tags, the preview compared with
the file); the other places are the picture's sources (``source_only``): the engine looks them up on the sites
(``source:…`` — ``engine._source_relay``).  Only its «exact» and «tossUp» answers count: «alternative» is another
version of the picture (another colour, without text…), «unlikely» — not it.
"""
from __future__ import annotations

import threading
import time

from core.parser.http import HttpFailure
from core.parser.models import Candidate, ReverseOutcome
from core.parser.reverse.base import ReverseService
from core.parser.util import json_body, looks_like_cloudflare, short

URL = "https://api.fluffle.xyz/v1/search"
GAP = 1.0                 # seconds between two searches (it says no limit: kept polite)
MATCHES = ("exact", "tossUp")
PLATFORMS = {             # Fluffle's name of a platform -> the words of the journal
    "e621": "e621", "furAffinity": "FurAffinity", "twitter": "Twitter", "bluesky": "Bluesky", "inkbunny": "Inkbunny",
    "weasyl": "Weasyl", "deviantArt": "DeviantArt", "furryNetwork": "Furry Network",
}


class Fluffle(ReverseService):
    name = "Fluffle"
    journal_key = "fluffle"
    host = "api.fluffle.xyz"
    trust_similarity = 101.0      # its hits are always compared with the file's picture (the preview of the post)
    # under it a hit may be another version of the picture (at the owner: «exact» at 60 %, the preview d=10 of the
    # run's 10 — most likely another version of the post's family): the preview must be nearly the file's
    strict_below = 80.0

    def __init__(self, ctx, sites: list):
        super().__init__(ctx, sites)
        self._turn = threading.Lock()
        self._last = 0.0

    def enabled(self) -> bool:
        return self.ctx.flag("enable_fluffle", True) and not self.disabled_reason

    def per_minute(self) -> float:
        host = super().per_minute()
        return min(host, 60.0 / GAP) if host else 60.0 / GAP

    def search(self, upload_path) -> ReverseOutcome:
        from core.parser.sites.base import API_VERSION
        try:
            with self._turn:
                left = self._last + GAP - time.monotonic()
                if left > 0:
                    self.http.sleep(left)
                try:
                    response = self.http.post_file(
                        URL, upload_path, data={"includeNsfw": "true", "limit": "16"},
                        headers={"Accept": "application/json",
                                 "User-Agent": f"LocalBooru/{API_VERSION} (local archive manager)"})
                finally:
                    self._last = time.monotonic()
        except HttpFailure as exc:
            return ReverseOutcome("failed", detail=exc.detail)
        status = response.status_code
        if status == 429:               # its own limit or Cloudflare's: later (three in a row — a pause, Availability)
            return ReverseOutcome("failed", detail="HTTP 429 — Fluffle просит реже")
        if looks_like_cloudflare(response):
            self.disable(f"Cloudflare (HTTP {status}) на fluffle.xyz")
            return ReverseOutcome("disabled", detail="Cloudflare")
        if status == 429 or status >= 500:
            return ReverseOutcome("failed", detail=f"HTTP {status}")
        if status in (400, 413, 415, 422):
            return ReverseOutcome("bad_input", detail=f"HTTP {status}: " + short(response.text, 120))
        if status in (401, 403, 404, 405, 410):          # the API refuses or is not there any more: not this file's fault
            self.disable(f"HTTP {status} по адресу поиска — сервис изменился или не пускает")
            return ReverseOutcome("disabled", detail=f"HTTP {status}")
        if status >= 400:
            return ReverseOutcome("failed", detail=f"HTTP {status}: " + short(response.text, 120))
        data = json_body(response)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return ReverseOutcome("failed", detail="непонятный ответ: " + short(response.text, 80))
        return self.outcome(data["results"])

    def outcome(self, results: list) -> ReverseOutcome:
        """What an answer gives: e621 posts as hits, the other places as the picture's sources."""
        out = ReverseOutcome("complete")
        seen = set()
        for row in results:
            if not isinstance(row, dict) or row.get("match") not in MATCHES:
                continue
            url = str(row.get("location") or "").strip()
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            try:
                score = round(float(row.get("score") or 0) * 100.0, 1)
            except (TypeError, ValueError):
                score = 0.0
            platform = str(row.get("platform") or "")
            thumb = row.get("thumbnail") if isinstance(row.get("thumbnail"), dict) else {}
            if platform == "e621":
                out.candidates.append(Candidate(url=url, similarity=score, service=self.name,
                                                preview_url=str(thumb.get("location") or "")))
            else:
                out.source_only.append({"url": url, "label": f"Fluffle {PLATFORMS.get(platform, platform)}",
                                        "similarity": score})
        for c in out.candidates[:3]:
            self.ctx.log(f"  Fluffle {c.similarity:.0f}% {c.url}")
        for s in out.source_only[:3]:
            self.ctx.log(f"  Fluffle {s['similarity']:.0f}% {s['label']}: {s['url']}")
        return out
