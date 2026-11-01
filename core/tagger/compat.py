"""Small facade with the old `Tagger` methods other modules still call.

The real parser lives in core/parser.  This class only adapts it for:
NO_MATCH manual URL (tags_from_url), browser-companion imports (process_image,
md5_lookup_all, extract_md5_candidates_from_post_url), maintenance category
recheck (honours `cancel_callback`), file-integrity recovery and the parser page.
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

from core.parser.context import ParserContext
from core.parser.http import BROWSER_UA, Cancelled, HttpClient, HttpFailure
from core.parser.util import clean_host, host_of, norm_md5
from core.tagger.tag_groups import empty_tag_groups, groups_to_tags, normalize_tag, unique_keep_order


def get_session(settings=None, log_func=None, target_host=None):
    """requests.Session with the user's saved cookies for `target_host`."""
    client = HttpClient(dict(settings or {}), log=log_func)
    if target_host:
        return client.session(clean_host(target_host))
    sess = requests.Session()
    sess.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json,text/html,*/*"})
    return sess


class Tagger:
    def __init__(self, settings, log_func=None):
        self.settings = settings if isinstance(settings, dict) else {}
        self.log = log_func or (lambda _m: None)
        try:
            self.timeout = max(5, int(float(self.settings.get("request_timeout_seconds", 30) or 30)))
        except (TypeError, ValueError):
            self.timeout = 30
        # Maintenance tasks assign a callable here; every request checks it (Stop button).
        self.cancel_callback = None
        self._last_md5_source_tag_groups: list = []
        self._http = HttpClient(self.settings, log=self.log, cancel_check=self._cancel_requested)
        self._ctx = ParserContext(self.settings, self._http, self.log)

    def _cancel_requested(self) -> bool:
        callback = self.cancel_callback
        if callback is None:
            return False
        try:
            return bool(callback())
        except Exception as exc:  # a broken callback must not stop lookups, but must be visible
            self.log(f"cancel_callback error: {type(exc).__name__}: {exc}")
            return False

    # ── sites ───────────────────────────────────────────────────────────────
    def _site_for_host(self, host: str):
        """A site object for any known host, even if it is unticked in the site table (no engine detection:
        callers ask host after host)."""
        from core.parser.sites import site_for_host
        return site_for_host(self._ctx, host, detect=False)

    def _all_enabled_site_configs(self):
        from core.parser.sites import build_sites
        return [dict(site.cfg, domain=site.host, engine=site.engine) for site in build_sites(self._ctx)]

    @staticmethod
    def _site_label(site):
        site = site if isinstance(site, dict) else {}
        return str(site.get("name") or site.get("domain") or site.get("base_url") or "site").strip() or "site"

    # ── URL -> tags ─────────────────────────────────────────────────────────
    def site_post_from_url(self, url):
        """The site's own post of a post page's URL — a parser ``Post``: the tags by category, the MD5, the uploader
        and the parent (``meta``); None — the site is not known, did not answer, has no such post."""
        site = self._site_for_host(host_of(url))
        if site is None:
            self.log(f"URL: сайт {host_of(url)} не поддерживается")
            return None
        from core.parser.sites import SiteUnavailable
        try:
            return site.post_from_url(url)
        except (SiteUnavailable, HttpFailure) as exc:
            self.log(f"URL: {site.label} недоступен — {exc}")
            return None
        except Cancelled:
            return None

    def grouped_tags_from_url(self, url):
        post = self.site_post_from_url(url)
        return post.groups if post else empty_tag_groups()

    def tags_from_url(self, url):
        return groups_to_tags(self.grouped_tags_from_url(url))

    def _categorize_flat_tags(self, source_host, tags):
        site = self._site_for_host(source_host or "")
        tags = [normalize_tag(t) for t in tags or [] if normalize_tag(t)]
        if site is not None and hasattr(site, "categorize"):
            try:
                return site.categorize(tags)
            except Cancelled:
                pass
        groups = empty_tag_groups()
        groups["general"] = tags
        return groups

    def md5_lookup_all(self, md5):
        """Exact MD5 on every enabled site -> (tags, source lines, [groups per site]).

        Also fills _last_md5_source_tag_groups (one tag set per site) for callers
        that store per-site provenance.
        """
        from core.parser.sites import SiteUnavailable, build_sites
        self._last_md5_source_tag_groups = []
        md5 = norm_md5(md5)
        if not md5:
            return [], [], []
        tags, sources, groups_list = [], [], []
        for site in build_sites(self._ctx):
            if not site.ready():
                continue
            try:
                post = site.search_md5(md5)
            except (SiteUnavailable, HttpFailure) as exc:
                self.log(f"MD5 {site.label}: недоступен — {exc}")
                continue
            except Cancelled:
                break
            if not post or not post.tags:
                continue
            tags += post.tags
            sources.append(f"md5 {post.label} {post.url}")
            groups_list.append(post.groups)
            self._last_md5_source_tag_groups.append({"url": post.url, "groups": post.groups, "method": "md5"})
        return unique_keep_order(tags), sources, groups_list

    def extract_md5_candidates_from_post_url(self, url):
        """[MD5 of the original file] for a booru post page URL, [] when unknown."""
        site = self._site_for_host(host_of(url))
        if site is None:
            return []
        from core.parser.sites import SiteUnavailable
        try:
            post = site.post_from_url(url)
        except (SiteUnavailable, HttpFailure) as exc:
            self.log(f"URL: {site.label} недоступен — {exc}")
            return []
        except Cancelled:
            return []
        return [post.md5] if post and post.md5 else []

    def session_for_host(self, host):
        return self._http.session(clean_host(host_of(host) or host))

    @staticmethod
    def _html_original_media_url(html_text, post_url):
        """Likely original media URL on a concrete HTML post page."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text or "", "html.parser")
        for sel in ("a#image-download-link", "a[href*='/data/original/']", "a[href*='/images/']",
                    "a[href*='/img/']", "source[src]", "video source[src]", "img#image", "img#post-image",
                    "img.image", "meta[property='og:image']", "meta[name='twitter:image']"):
            el = soup.select_one(sel)
            value = el and (el.get("href") or el.get("src") or el.get("content"))
            if value:
                return urljoin(post_url, _html.unescape(str(value)))
        m = re.search(r'''https?://[^"'<>\s]+\.(?:jpg|jpeg|png|gif|webp|mp4|webm)(?:\?[^"'<>\s]*)?''', html_text or "", re.I)
        return m.group(0) if m else ""

    # ── one file through the full parser ───────────────────────────────────
    def process_image(self, img, persist_lock=None):
        """Old return values: tagged | nomatch | skip | retry_network."""
        from concurrent.futures import ThreadPoolExecutor
        from core.parser.engine import FileJob, ParserEngine
        from core.parser.http import Cancelled
        from core.parser.reverse import build_reverse
        from core.parser.sites import build_sites
        sites = build_sites(self._ctx)
        with ThreadPoolExecutor(max_workers=max(2, min(8, len(sites) or 2)), thread_name_prefix="parser-site") as pool:
            engine = ParserEngine(self._ctx, sites, build_reverse(self._ctx, sites), pool)
            try:
                result = engine.process(FileJob(path=Path(img), index=1, original_path=str(img)))
            except Cancelled:
                return "skip"
        return {"tagged": "tagged", "nomatch": "nomatch", "skip": "skip"}.get(result.status, "retry_network")
