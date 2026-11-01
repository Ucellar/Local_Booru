"""Base class for one booru site: exact-MD5 search, post fetch, tag parsing."""
from __future__ import annotations

from core.parser.availability import Availability
from core.parser.context import ParserContext
from core.parser.http import HttpFailure
from core.parser.models import Post
from core.parser.util import clean_host, failure_envelope, json_body, looks_like_cloudflare, norm_md5, post_id_from_url, short
from core.tagger.tag_groups import add_tags_to_groups, empty_tag_groups, groups_to_tags, normalize_tag

from core.site_agent import VERSION as API_VERSION  # one name and version of the program in every User-Agent


class SiteUnavailable(Exception):
    """The site did not answer this request.

    transient=True  — network, rate limit, maintenance: the file is deferred, never sent to Брак;
    transient=False — the site refused (bad key, rejected query): this request is simply lost.
    """

    def __init__(self, reason: str, *, transient: bool = True):
        super().__init__(reason)
        self.transient = transient


class TextBody(str):
    """A 2xx answer that is not JSON (XML, HTML, plain text), returned only on request."""


class Site(Availability):
    engine = "base"
    post_path = ""            # the post page under the site root, "{id}" — the post id
    aliases: tuple = ()
    md5_batch_limit = 0       # MD5 per request of search_md5_many (0: the engine has no such query)
    md5_parallel = 1          # single MD5 lookups the site's queue may have open at once (core/parser/runner.py)

    def __init__(self, domain: str, cfg: dict, ctx: ParserContext):
        self.host = clean_host(domain)
        self.cfg = dict(cfg or {})
        self.ctx = ctx
        self.http = ctx.http
        self.root = f"https://{self.host}"
        self._init_availability(ctx.log)

    @property
    def label(self) -> str:
        return str(self.cfg.get("name") or self.host)

    # ── configuration ───────────────────────────────────────────────────────
    @property
    def login(self) -> str:
        return str(self.cfg.get("login") or "").strip()

    @property
    def api_key(self) -> str:
        return str(self.cfg.get("api_key") or "").strip()

    @property
    def user_id(self) -> str:
        return str(self.cfg.get("user_id") or "").strip()

    def handles(self, host: str) -> bool:
        h = clean_host(host)
        return h == self.host or h in self.aliases

    def api_user_agent(self) -> str:
        custom = str(self.cfg.get("user_agent") or self.cfg.get("api_user_agent") or "").strip()
        if custom:
            return custom
        who = f"by {self.login} on {self.host}" if self.login else "local archive manager"
        return f"LocalBooru/{API_VERSION} ({who})"

    def preflight(self) -> None:
        """Disable the site up front when its configuration can never work."""

    # ── interface implemented by engines ───────────────────────────────────
    def search_md5(self, md5: str) -> Post | None:
        raise NotImplementedError

    def fetch_post(self, post_id: str) -> Post | None:
        raise NotImplementedError

    def post_url(self, post_id: str) -> str:
        if not self.post_path:
            raise NotImplementedError
        return self.root + self.post_path.format(id=post_id)

    def search_query(self, query: str, limit: int = 3) -> list:
        """Tag-query search (used for pixiv_id:/source: relays). Optional."""
        return []

    tag_page_max = 100            # posts one page of a tag search may hold on this engine
    can_search_tags = False       # the grabber offers only sites whose engine pages through a tag search

    def search_tags(self, query: str, page: int = 0, limit: int = 40) -> list:
        """One page of a tag search, newest first: [(Post, raw post dict)]; ``page`` counts from 0.
        Used by the grabber; the raw dict goes to the downloader (it knows the original's URL)."""
        return []

    md5_batch_style = ""          # the way of asking for many MD5 that the run's 2/2 check proved (core/parser/batch.py)

    def md5_batch_styles(self) -> tuple:
        """The ways this engine can ask for many MD5 at once, the likeliest first (the 2/2 check tries them in turn)."""
        return ("",)

    def search_md5_many(self, md5s: list, style: str = "", probe: bool = False) -> tuple[dict, bool]:
        """Several exact MD5 in one request: ({md5: Post}, complete).  ``style`` — one of ``md5_batch_styles`` (""
        — the proved one); ``probe`` — the 2/2 check: a refusal must not switch the site off.

        complete=False: the answer cannot prove that a missing MD5 is not on the site (a post
        hid its MD5, or the site answered with posts nobody asked for).  Never trusted before
        the run has checked the site's syntax (core/parser/batch.py)."""
        raise NotImplementedError

    id_batch_limit = 0            # post numbers per request of posts_by_ids (0: one post per request)

    def id_batch_styles(self) -> tuple:
        """The ways this engine may ask for several posts by number in one request, the likeliest first.
        core/site_posts.py trusts one only after the site answered it with two posts it surely has and nothing else."""
        return ()

    def posts_by_ids(self, ids: list, style: str = "") -> list:
        """The site's raw posts of these numbers, in one request (``style`` — one of ``id_batch_styles``; one number —
        the plain query).  The answer may hold posts nobody asked for when the site did not understand the query: the
        caller maps it by the number each post carries, never by position."""
        return []

    def _map_md5_answer(self, raws: list, wanted: list, md5_of, parse) -> tuple[dict, bool]:
        """Posts of a multi-MD5 answer, each by the MD5 it publishes — never by its position."""
        wanted_set = set(wanted)
        found: dict = {}
        complete = True
        for raw in raws:
            remote = norm_md5(md5_of(raw))
            if not remote:
                complete = False                # Danbooru hides the MD5 of restricted posts
                continue
            if remote not in wanted_set:
                complete = False                # not what was asked: the site did not understand the query
                continue
            if remote not in found:
                post = parse(raw)
                if post and post.tags:
                    found[remote] = post
        return found, complete

    def post_from_url(self, url: str) -> Post | None:
        post_id = post_id_from_url(url)
        return self.fetch_post(post_id) if post_id else None

    # ── helpers for engines ─────────────────────────────────────────────────
    def get_json(self, url: str, *, params=None, headers=None, auth=None,
                 soft: bool = False, accept_text: bool = False):
        """GET an API URL and return parsed JSON (dict or list); None for HTTP 404.

        Anything that is not a real answer raises SiteUnavailable (see its docstring).
        soft=True: optional relay query — a refusal must not switch the whole site off.
        accept_text=True: return a TextBody for 2xx answers that are not JSON
        (Gelbooru-style XML, the ATF verification page) instead of raising.
        """
        try:
            response = self.http.get(url, params=params, headers=headers, auth=auth)
        except HttpFailure as exc:
            raise SiteUnavailable(f"сеть: {exc.detail}") from exc
        status = response.status_code
        if looks_like_cloudflare(response):
            # A Cloudflare wall does not go away by itself: stop asking until the user
            # refreshes cookies.  Files are not held back because of it (see Availability).
            self.disable(f"Cloudflare (HTTP {status}) — открой сайт в браузере, пройди проверку "
                         "и обнови cookies через расширение Companion")
            raise SiteUnavailable(f"Cloudflare (HTTP {status})", transient=False)
        if status in (401, 403):
            if not soft:
                self.disable(f"HTTP {status}: доступ запрещён — проверь логин и API-ключ сайта")
            raise SiteUnavailable(f"HTTP {status}", transient=False)
        if status == 404:
            return None
        if status == 429 or status >= 500:
            raise SiteUnavailable(f"HTTP {status}")
        if status >= 400:
            raise SiteUnavailable(f"HTTP {status}: запрос отклонён", transient=False)
        data = json_body(response)
        if data is None:
            text = (response.text or "").strip()
            if not text or text in ("[]", "{}"):
                return []
            if accept_text:
                return TextBody(text)
            raise SiteUnavailable("ответ не JSON (страница обслуживания или входа?): " + short(text, 80))
        kind, reason = failure_envelope(data)
        if kind == "auth_required":
            if not soft:
                self.disable(f"API требует авторизацию: {short(reason)}")
            raise SiteUnavailable(reason, transient=False)
        if kind:
            raise SiteUnavailable(f"API ошибка: {short(reason)}")
        return data

    def groups_from_fields(self, post: dict, mapping: dict) -> dict:
        """Build tag groups from split string/list fields, e.g. tag_string_artist."""
        groups = empty_tag_groups()
        for group, keys in mapping.items():
            for key in keys:
                value = post.get(key)
                if isinstance(value, str):
                    add_tags_to_groups(groups, group, value.split())
                elif isinstance(value, list):
                    add_tags_to_groups(groups, group, [str(v) for v in value])
        return groups

    @staticmethod
    def flat_groups(tags) -> dict:
        groups = empty_tag_groups()
        add_tags_to_groups(groups, "general", [normalize_tag(t) for t in tags or [] if normalize_tag(t)])
        return groups

    @staticmethod
    def has_tags(groups: dict) -> bool:
        return bool(groups_to_tags(groups))
