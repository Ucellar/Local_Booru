"""rule34.us: no JSON API, so search the HTML and accept a post only after MD5 proof."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from core.parser.http import HttpFailure
from core.parser.models import Post
from core.parser.sites.base import Site, SiteUnavailable
from core.parser.util import is_post_url, looks_like_cloudflare, post_id_from_url
from core.tagger.tag_groups import add_tags_to_groups, empty_tag_groups, normalize_tag

_CATEGORY_CLASSES = {
    "artist": ("artist", "tag-type-artist", "tag-type-1"),
    "character": ("character", "tag-type-character", "tag-type-4"),
    "copyright": ("copyright", "tag-type-copyright", "tag-type-3"),
    "meta": ("metadata", "tag-type-metadata", "tag-type-meta", "tag-type-5"),
}


class Rule34UsSite(Site):
    engine = "rule34us"
    post_path = "/index.php?r=posts/view&id={id}"


    def _html(self, url: str, params=None) -> str:
        try:
            r = self.http.get(url, params=params, headers={"Accept": "text/html,*/*"})
        except HttpFailure as exc:
            raise SiteUnavailable(f"сеть: {exc.detail}") from exc
        if looks_like_cloudflare(r):
            self.disable(f"Cloudflare (HTTP {r.status_code}) — открой сайт в браузере, пройди проверку "
                         "и обнови cookies через расширение Companion")
            raise SiteUnavailable("Cloudflare", transient=False)
        if r.status_code == 404:
            return ""
        if r.status_code == 429 or r.status_code >= 500:
            raise SiteUnavailable(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise SiteUnavailable(f"HTTP {r.status_code}", transient=False)
        return r.text or ""

    def search_md5(self, md5: str) -> Post | None:
        from bs4 import BeautifulSoup
        for params in ({"r": "posts/index", "q": f"md5:{md5}"}, {"page": "post", "s": "list", "tags": f"md5:{md5}"}):
            soup = BeautifulSoup(self._html(f"{self.root}/index.php", params), "html.parser")
            for a in soup.select("a[href]"):
                href = urljoin(self.root + "/", a.get("href", ""))
                if is_post_url(href) and "rule34.us" in href:
                    post = self._post_page(post_id_from_url(href), wanted_md5=md5)
                    if post:
                        return post
                    break  # first post only; others are unrelated results
        return None

    def fetch_post(self, post_id: str) -> Post | None:
        return self._post_page(post_id, wanted_md5="") if str(post_id).isdigit() else None

    def _post_page(self, post_id: str, wanted_md5: str) -> Post | None:
        from bs4 import BeautifulSoup
        url = self.post_url(post_id)
        html = self._html(url)
        soup = BeautifulSoup(html, "html.parser")
        media = _original_media_url(soup, url)
        page_md5 = _md5_in_media_url(media)
        if wanted_md5:
            if page_md5 and page_md5 != wanted_md5:
                return None
            if not page_md5 and not self._remote_md5_matches(media, wanted_md5):
                return None
        groups = _groups_from_html(soup)
        if not self.has_tags(groups):
            return None
        return Post(site=self.host, label=self.label, post_id=str(post_id), url=url,
                    md5=wanted_md5 or page_md5, groups=groups, file_url=media)

    def _remote_md5_matches(self, media_url: str, wanted: str) -> bool:
        if not media_url:
            return False
        from core.download_policy import downloads_enabled
        if not downloads_enabled(self.ctx.settings):
            return False
        try:
            data = self.http.get_bytes(media_url, max_bytes=80 * 1024 * 1024)
        except HttpFailure:
            return False
        return hashlib.md5(data).hexdigest() == wanted


def _original_media_url(soup, base: str) -> str:
    for sel in ("a#image-download-link", "video source[src]", "img#image", "img#img", "meta[property='og:image']"):
        el = soup.select_one(sel)
        if el:
            value = el.get("href") or el.get("src") or el.get("content")
            if value:
                return urljoin(base, value)
    return ""


def _md5_in_media_url(url: str) -> str:
    name = unquote(urlparse(url or "").path.rsplit("/", 1)[-1])
    m = re.search(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", name.lower())
    return m.group(1) if m else ""


def _groups_from_html(soup) -> dict:
    groups = empty_tag_groups()
    links = soup.select("#tag-list a[href], .tag-list a[href], ul.tags a[href], "
                        "li[class*='tag-type'] a[href], aside a[href]") or soup.select("a[href]")
    for a in links:
        href = a.get("href", "") or ""
        qs = parse_qs(urlparse(href).query)
        values = qs.get("q") or qs.get("tags") or []
        if not values or ("r" in qs and qs.get("r", [""])[0] != "posts/index"):
            continue
        tags = [normalize_tag(t) for t in " ".join(values).replace("+", " ").split()]
        tags = [t for t in tags if t and ":" not in t]
        if len(tags) != 1:
            continue
        holder = a.find_parent(["li", "div", "span"]) or a
        classes = " ".join(holder.get("class") or []).lower()
        group = next((g for g, names in _CATEGORY_CLASSES.items() if any(n in classes for n in names)), "general")
        add_tags_to_groups(groups, group, tags)
    return groups
