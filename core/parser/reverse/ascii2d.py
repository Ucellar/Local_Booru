"""ascii2d.net (colour + feature search). Mostly finds Pixiv/Twitter sources,
which the engine then relays to Danbooru. Often behind Cloudflare: optional."""
from __future__ import annotations

from urllib.parse import urljoin

from core.parser.http import HttpFailure
from core.parser.models import Candidate, ReverseOutcome
from core.parser.reverse.base import ReverseService
from core.parser.util import looks_like_cloudflare

BASE = "https://ascii2d.net/"


class Ascii2d(ReverseService):
    name = "Ascii2D"
    host = "ascii2d.net"

    def enabled(self) -> bool:
        return self.ctx.flag("enable_ascii2d", False) and not self.disabled_reason

    def search(self, upload_path) -> ReverseOutcome:
        from bs4 import BeautifulSoup
        try:
            home = self.http.get(BASE, headers={"Accept": "text/html"})
            if looks_like_cloudflare(home):
                return self._cloudflare()
            token_el = BeautifulSoup(home.text or "", "html.parser").select_one("input[name=authenticity_token]")
            token = token_el.get("value", "") if token_el else ""
            response = self.http.post_file(urljoin(BASE, "search/file"), upload_path,
                                           data={"utf8": "✓", "authenticity_token": token})
        except HttpFailure as exc:
            return ReverseOutcome("failed", detail=exc.detail)
        status = response.status_code
        if looks_like_cloudflare(response):
            return self._cloudflare()
        if status == 429 or status >= 500:
            return ReverseOutcome("failed", detail=f"HTTP {status}")
        if status >= 400:
            return ReverseOutcome("bad_input", detail=f"HTTP {status}")
        outcome = ReverseOutcome("complete")
        outcome.candidates = self._parse(response.text or "", response.url)
        if "/color/" in str(response.url):
            try:
                bovw = self.http.get(str(response.url).replace("/color/", "/bovw/"), headers={"Accept": "text/html"})
                if bovw.status_code < 400 and not looks_like_cloudflare(bovw):
                    outcome.candidates += self._parse(bovw.text or "", bovw.url)
            except HttpFailure:
                pass
        for c in outcome.candidates[:5]:
            self.ctx.log(f"  Ascii2D: {c.url}")
        return outcome

    def _cloudflare(self) -> ReverseOutcome:
        self.disable("Cloudflare — открой ascii2d.net в браузере, пройди проверку и обнови cookies через Companion")
        return ReverseOutcome("disabled", detail="Cloudflare")

    def _parse(self, html: str, page_url: str) -> list:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for box in soup.select("div.item-box")[1:12]:   # the first box is the uploaded image
            img = box.select_one("img")
            thumb = urljoin(page_url, img.get("src", "")) if img else ""
            for a in box.select(".detail-box a[href]"):
                href = a.get("href", "")
                if href.startswith("http") and any(h in href for h in ("pixiv.net", "twitter.com", "x.com", "fanbox", "fantia", "nijie", "seiga", "tinami", "skeb")):
                    out.append(Candidate(url=href, similarity=0.0, service=self.name, preview_url=thumb))
                    break
        return out
