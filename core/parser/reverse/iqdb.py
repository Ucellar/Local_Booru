"""iqdb.org (multi-booru) and danbooru.iqdb.org: HTML result tables."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from core.parser.http import HttpFailure
from core.parser.models import Candidate, ReverseOutcome
from core.parser.reverse.base import ReverseService
from core.parser.util import host_of, looks_like_cloudflare, norm_md5, page_words

_SIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*similarity", re.I)
# Any of these proves we got an IQDB result page (with or without matches).
_RESULT_PAGE_MARKERS = ("your image", "no relevant matches", "% similarity", 'id="pages"', "id='pages'")
# iqdb.org's own words on a search it did not finish: «Can't read query result! Please try again.» (the owner's journal
# of 1.11.2026: five of about a hundred searches).  (v5.44.43) The owner (2.11.2026): «iqdb.org должен работать, так
# что чини» — it was asked once more and then given up (the file deferred, and a run where iqdb.org was shaky left it
# in Брак).  Now it is asked again several times, the wait growing (iqdb.org clears it on a retry — the owner's log:
# «…ещё раз через 5 с» then a 95 % hit); only after :data:`TRY_AGAIN_WAITS` are all spent is it a failure (deferred).
_TRY_AGAIN = ("can't read query result", "please try again")
TRY_AGAIN_WAITS = (5.0, 10.0, 20.0)


class Iqdb(ReverseService):
    name = "IQDB"
    journal_key = "iqdb"
    host = "iqdb.org"
    endpoint = "https://iqdb.org/"
    setting = "enable_iqdb"
    default_enabled = True
    # (v5.44.36) its videos are Danbooru's (their previews: the frame of «thumbnail» over the first 300 frames — two of
    # the owner's Брак found by it at 87 % and 99 %, by their start not), then Gelbooru's and Sankaku's: the start
    video_styles = ("danbooru", "start")

    def enabled(self) -> bool:
        return self.ctx.flag(self.setting, self.default_enabled) and not self.disabled_reason

    def min_similarity(self) -> float:
        return self.ctx.setting_float("iqdb_min_similarity", 80.0, 0.0, 100.0)

    def search(self, upload_path, *, again: int = 0) -> ReverseOutcome:
        try:
            response = self.http.post_file(self.endpoint, upload_path, data={"forcegray": "on"})
        except HttpFailure as exc:
            return ReverseOutcome("failed", detail=exc.detail)
        status = response.status_code
        if looks_like_cloudflare(response):
            self.disable(f"Cloudflare (HTTP {status}) на {self.endpoint}")
            return ReverseOutcome("disabled", detail="Cloudflare")
        if status == 429 or status >= 500:
            return ReverseOutcome("failed", detail=f"HTTP {status}")
        if status >= 400:
            return ReverseOutcome("bad_input", detail=f"HTTP {status}")
        html = response.text or ""
        low = html.lower()
        if "not an image" in low or "too large" in low or "file is too" in low:
            return ReverseOutcome("bad_input", detail=page_words(html, 160))
        if not any(m in low for m in _RESULT_PAGE_MARKERS):
            if again < len(TRY_AGAIN_WAITS) and any(words in low for words in _TRY_AGAIN):
                wait = TRY_AGAIN_WAITS[again]
                self.ctx.log(f"  {self.name}: «Can't read query result» — спрашиваю ещё раз ({again + 1} из "
                             f"{len(TRY_AGAIN_WAITS)}) через {wait:.0f} с")
                self.http.sleep(wait)
                return self.search(upload_path, again=again + 1)
            # what the page says (its title and text), not the beginning of its markup — whole enough to tell why
            # (v5.27 cut it at «Multi-service image search - Search results — Multi-service image search ( 0…»)
            return ReverseOutcome("failed",
                                  detail="вместо результатов пришла другая страница: " + page_words(html, 400))
        outcome = ReverseOutcome("complete")
        outcome.candidates = self.parse(html)
        for c in outcome.candidates[:5]:
            self.ctx.log(f"  {self.name} {c.similarity:.0f}% {c.url}")
        return outcome

    def parse(self, html: str) -> list:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.select("#pages table") or soup.select("table")
        min_sim = self.min_similarity()
        out, seen = [], set()
        for table in tables:
            text = table.get_text(" ", strip=True)
            if "your image" in text.lower():
                continue
            m = _SIM_RE.search(text)
            if not m:
                continue
            sim = float(m.group(1))
            if sim < min_sim:
                continue
            for a in table.select("a[href]"):
                url = _absolute(a.get("href", ""), self.endpoint)
                url = _as_candidate_url(url)
                if not url or url in seen:
                    continue
                seen.add(url)
                out.append(Candidate(url=url, similarity=sim, service=self.name))
        out.sort(key=lambda c: c.similarity, reverse=True)
        return out


class DanbooruIqdb(Iqdb):
    name = "Danbooru IQDB"
    journal_key = "danbooru_iqdb"
    host = "danbooru.iqdb.org"
    endpoint = "https://danbooru.iqdb.org/"
    setting = "enable_danbooru_iqdb"
    default_enabled = False
    video_styles = ("danbooru",)         # Danbooru's alone


def _absolute(href: str, base: str) -> str:
    href = (href or "").strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return href


def _as_candidate_url(url: str) -> str:
    """Keep post pages; turn 'list?md5=' links into md5: hints; drop the rest."""
    host = host_of(url)
    if not host or host.endswith("iqdb.org"):
        return ""
    qs = parse_qs(urlparse(url).query)
    md5 = norm_md5((qs.get("md5") or [""])[0])
    if md5:
        return f"md5:{md5}"
    path = urlparse(url).path.lower()
    if "/posts/" in path or "/post/show/" in path or ("page=post" in url and "s=view" in url):
        return url
    return ""
