"""Gelbooru 0.2 DAPI: gelbooru.com, rule34.xxx, safebooru.org, xbooru, realbooru, tbib, hypnohub.

Post endpoints return one flat "tags" string, so categories come from the
tag-index endpoint and are cached in SQLite (tag_category_cache) per host.
rule34.xxx sends the category of every tag with the post itself when asked
(``fields=tag_info``): no tag requests at all there.
"""
from __future__ import annotations

import re

from core.parser.http import HttpFailure
from core.parser.models import Post
from core.parser.sites.base import Site, SiteUnavailable, TextBody
from core.parser.util import norm_md5, post_dicts, posts_from_xml, short, xml_elements
from core.site_posts import fields as post_fields
from core.tagger.tag_groups import add_tags_to_groups, empty_tag_groups, group_from_tag_type, normalize_tag

# Hosts whose tag index reliably returns categories.
_CATEGORY_HOSTS = {"gelbooru.com", "rule34.xxx", "xbooru.com", "hypnohub.net", "safebooru.org", "tbib.org", "realbooru.com"}


class GelbooruSite(Site):
    engine = "gelbooru"
    post_path = "/index.php?page=post&s=view&id={id}"
    md5_batch_limit = 40

    def __init__(self, domain, cfg, ctx):
        super().__init__(domain, cfg, ctx)
        self.is_rule34 = self.host in ("rule34.xxx", "api.rule34.xxx")
        if self.is_rule34:
            self.host = "rule34.xxx"
            self.aliases = ("api.rule34.xxx",)
            self.root = "https://rule34.xxx"
        self.api = "https://api.rule34.xxx/index.php" if self.is_rule34 else f"{self.root}/index.php"
        if self.is_rule34:
            # its API takes several requests at once (core.http_rate_limiter.parallel_for, the gate of core/parser/http.py)
            from core.http_rate_limiter import parallel_for
            self.md5_parallel = parallel_for("api.rule34.xxx", ctx.settings)

    def preflight(self) -> None:
        if self.is_rule34 and not (self.api_key and self.user_id):
            self.disable("rule34.xxx требует user_id и api_key (страница аккаунта → Options → API Access)")


    # ── DAPI ────────────────────────────────────────────────────────────────
    def _auth_params(self) -> dict:
        params = {}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.user_id:
            params["user_id"] = self.user_id
        return params

    def _headers(self) -> dict | None:
        if self.is_rule34:
            return {"Accept": "application/json, text/xml, */*", "User-Agent": self.api_user_agent()}
        return {"Accept": "application/json, text/xml, */*"}

    def _dapi(self, s: str, *, soft: bool = False, **extra):
        if self.is_rule34 and not (self.api_key and self.user_id):
            self.disable("rule34.xxx требует user_id и api_key (страница аккаунта → Options → API Access)")
            raise SiteUnavailable("нет user_id/api_key", transient=False)
        params = {"page": "dapi", "s": s, "q": "index", "json": "1", **extra, **self._auth_params()}
        if self.is_rule34 and s == "post" and "tags" in extra:
            params["fields"] = "tag_info"        # the category of every tag comes with the post
        data = self.get_json(self.api, params=params, headers=self._headers(), soft=soft, accept_text=True)
        if isinstance(data, TextBody):
            self._check_text_answer(str(data), soft=soft)
        return data

    def _check_text_answer(self, text: str, *, soft: bool) -> None:
        """DAPI answered with text: valid XML results pass, anything else must not count as 'not found'."""
        low = text[:800].lower().lstrip()
        if "missing authentication" in low or ("api.rule34.xxx" in low and "authentication" in low):
            if not soft:
                self.disable("API отвечает «Missing authentication» — проверь user_id и api_key")
            raise SiteUnavailable("missing authentication", transient=False)
        for row in xml_elements(text, "response"):
            if str(row.get("success", "")).strip().lower() != "false":
                continue
            reason = str(row.get("reason") or row.get("message") or "API success=false")
            if any(w in reason.lower() for w in ("authentication", "api key", "unauthorized", "forbidden", "login")):
                if not soft:
                    self.disable(f"API требует авторизацию: {short(reason)}")
                raise SiteUnavailable(reason, transient=False)
            raise SiteUnavailable(f"API ошибка: {short(reason)}")   # e.g. "API limited due to abuse"
        if low.startswith(("<!doctype html", "<html")) or "<body" in low:
            raise SiteUnavailable("HTML вместо ответа API (страница обслуживания или входа?)")
        if not (low.startswith(("<?xml", "<posts", "<tags", "<post ", "<tag ")) or "<posts" in low or "<tags" in low):
            raise SiteUnavailable("непонятный ответ API: " + short(text, 80))

    def _posts(self, data) -> list:
        if isinstance(data, TextBody):
            return posts_from_xml(str(data))
        return post_dicts(data, keys=("post", "posts"))

    def search_md5(self, md5: str) -> Post | None:
        data = self._dapi("post", tags=f"md5:{md5}", limit=1)
        posts = self._posts(data)
        for raw in posts:
            remote = norm_md5(raw.get("md5") or raw.get("hash"))
            if remote == md5:
                return self.parse(raw)
        if posts:
            # rule34.xxx sometimes ignores md5: and returns the newest posts.
            self.ctx.log(f"    {self.label}: API вернул посты без нужного MD5 — ответ проигнорирован")
        return None

    def md5_batch_styles(self) -> tuple:
        """OR of md5: metatags, the likeliest syntax first.  Gelbooru writes OR as ``{a ~ b}`` now (its old syntax was
        ``~a ~b``); rule34.xxx — ``( a ~ b )``.  Neither site promises that metatags work inside OR, so the run uses
        one only after the site answered 2 of 2 known MD5 with it (core/parser/batch.py)."""
        return ("parens", "braces") if self.is_rule34 else ("braces", "tilde")

    def search_md5_many(self, md5s: list, style: str = "", probe: bool = False) -> tuple[dict, bool]:
        style = style or self.md5_batch_style or self.md5_batch_styles()[0]
        terms = [f"md5:{m}" for m in md5s]
        if style == "braces":
            query = "{" + " ~ ".join(terms) + "}"
        elif style == "parens":
            query = "( " + " ~ ".join(terms) + " )"
        else:
            query = " ".join("~" + term for term in terms)
        posts = self._posts(self._dapi("post", soft=probe, tags=query, limit=len(md5s)))
        return self._map_md5_answer(posts, md5s, lambda raw: raw.get("md5") or raw.get("hash"), self.parse)

    def post_id_by_image_key(self, key: str, exts: list) -> str:
        """rule34.xxx names its files by a 40-hex image key, not by MD5: a saved sample
        (``sample_<key>.jpg``) has another MD5 than the post's original but the same key.
        ``hl.rule34.xxx/public/hotlink.php?img=<key>.<ext>`` redirects to the post page."""
        if not self.is_rule34:
            return ""
        headers = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Upgrade-Insecure-Requests": "1"}
        for ext in exts:
            url = f"https://hl.rule34.xxx/public/hotlink.php?img={key}.{ext}"
            try:
                response = self.http.get(url, headers=headers, allow_redirects=False, attempts=1,
                                         session_host=self.host)      # the cookies of rule34.xxx
            except HttpFailure as exc:
                raise SiteUnavailable(f"сеть: {exc.detail}") from exc
            target = str(response.headers.get("Location") or "")
            m = re.search(r"[?&]id=(\d+)", target)
            if response.status_code in (301, 302, 303, 307, 308) and "page=post" in target and m:
                return m.group(1)
            if response.status_code == 429 or response.status_code >= 500:
                raise SiteUnavailable(f"hotlink.php: HTTP {response.status_code}")
        return ""

    id_batch_limit = 20

    def id_batch_styles(self) -> tuple:
        """OR of id: metatags — the syntaxes of the MD5 packs (rule34.xxx ``( a ~ b )``, Gelbooru ``{a ~ b}``)."""
        return ("parens", "braces") if self.is_rule34 else ("braces", "tilde")

    def posts_by_ids(self, ids: list, style: str = "") -> list:
        if len(ids) == 1:
            return self._posts(self._dapi("post", id=str(ids[0])))
        terms = [f"id:{i}" for i in ids]
        if style == "braces":
            query = "{" + " ~ ".join(terms) + "}"
        elif style == "parens":
            query = "( " + " ~ ".join(terms) + " )"
        else:
            query = " ".join("~" + term for term in terms)
        return self._posts(self._dapi("post", soft=True, tags=query, limit=len(ids)))

    def fetch_post(self, post_id: str) -> Post | None:
        if not str(post_id).isdigit():
            return None
        posts = self._posts(self._dapi("post", id=str(post_id)))
        return self.parse(posts[0]) if posts else None

    def search_query(self, query: str, limit: int = 3) -> list:
        posts = self._posts(self._dapi("post", soft=True, tags=query, limit=max(1, min(20, limit))))
        return [p for p in (self.parse(raw) for raw in posts) if p and p.tags]

    can_search_tags = True

    def search_tags(self, query: str, page: int = 0, limit: int = 40) -> list:
        # Categories of a whole page would cost tag-API requests: the grabber asks for them when
        # a post is opened (categorize).
        data = self._dapi("post", tags=query, limit=max(1, min(self.tag_page_max, limit)), pid=int(page))
        return [(post, raw) for raw in self._posts(data) for post in [self.parse(raw, categorize=False)] if post]

    def parse(self, raw: dict, *, categorize: bool = True) -> Post | None:
        if not isinstance(raw, dict) or not raw.get("id"):
            return None
        tags = [normalize_tag(t) for t in str(raw.get("tags") or "").split() if normalize_tag(t)]
        if not tags:
            return None
        post_id = str(raw.get("id"))
        source = str(raw.get("source") or "").strip()
        sent = self._sent_categories(raw, tags)
        if sent is not None:
            groups = empty_tag_groups()
            for tag in tags:
                add_tags_to_groups(groups, sent.get(tag, "general"), [tag])
        elif categorize:
            groups = self.categorize(tags)
        else:
            groups = empty_tag_groups()
            add_tags_to_groups(groups, "general", tags)
        return Post(
            site=self.host, label=self.label, post_id=post_id, url=self.post_url(post_id),
            md5=norm_md5(raw.get("md5") or raw.get("hash")), groups=groups,
            rating=str(raw.get("rating") or ""), sources=[source] if source.startswith("http") else [],
            preview_url=str(raw.get("preview_url") or raw.get("sample_url") or ""),
            file_url=str(raw.get("file_url") or ""),
            meta=post_fields(raw, self.engine),
        )

    # ── tag categories ──────────────────────────────────────────────────────
    def _sent_categories(self, raw: dict, tags: list) -> dict | None:
        """Categories the post came with (rule34.xxx ``tag_info``: [{"tag", "type", "count"}]), or None.  A tag the
        list left out is looked up in the category cache — never on the site.  What came is kept in the cache for
        posts that come without it."""
        rows = raw.get("tag_info")
        if not isinstance(rows, list):
            return None
        wanted = set(tags)
        sent = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = normalize_tag(row.get("tag") or row.get("name") or "")
            if name in wanted and row.get("type") is not None:
                sent[name] = group_from_tag_type(row.get("type"))
        if sent:
            self._store_categories(sent, method="post_tag_info")
        rest = [t for t in tags if t not in sent]
        if rest:
            sent.update({t: g for t, g in self._known_categories(rest).items() if g})
        return sent

    def categorize(self, tags: list) -> dict:
        groups = empty_tag_groups()
        if self.host not in _CATEGORY_HOSTS or not self.ctx.flag("parser_tag_categories_enabled", True):
            add_tags_to_groups(groups, "general", tags)
            return groups
        known = self._known_categories(tags)
        missing = [t for t in tags if t not in known]
        if missing:
            learned = self._lookup_categories(missing)
            known.update(learned)
            if learned:
                self._store_categories(learned)
        for tag in tags:
            add_tags_to_groups(groups, known.get(tag, "general"), [tag])
        return groups

    def _known_categories(self, tags: list) -> dict:
        ctx = self.ctx
        with ctx.tag_categories_lock:
            memo = ctx.tag_categories.setdefault(self.host, {})
            known = {t: memo[t] for t in tags if t in memo}
        rest = [t for t in tags if t not in known]
        if rest:
            try:
                from core.database.storage import cached_tag_categories
                cached = cached_tag_categories(ctx.settings, self.host, rest) or {}
            except Exception as exc:
                ctx.log(f"    {self.label}: кэш категорий недоступен: {exc}")
                cached = {}
            known.update({k: v or "general" for k, v in cached.items()})
            with ctx.tag_categories_lock:
                ctx.tag_categories[self.host].update(cached)
        return known

    def _lookup_categories(self, tags: list) -> dict:
        found: dict = {}
        for i in range(0, len(tags), 50):
            chunk = tags[i:i + 50]
            try:
                found.update(self._rows_to_categories(self._dapi("tag", soft=True, names=" ".join(chunk), limit=len(chunk)), chunk))
            except SiteUnavailable as exc:
                self.ctx.log(f"    {self.label}: категории тегов не получены: {short(exc)}")
                return found
        # Some installs ignore names=; ask one by one for what is left (bounded).
        budget = self.ctx.setting_int("parser_tag_category_single_lookups", 30, 0, 500)
        for tag in [t for t in tags if t not in found][:budget]:
            try:
                found.update(self._rows_to_categories(self._dapi("tag", soft=True, name=tag), [tag]))
            except SiteUnavailable:
                break
        return found

    def _rows_to_categories(self, data, wanted: list) -> dict:
        rows = []
        if isinstance(data, TextBody):
            rows = xml_elements(str(data), "tag")
        elif isinstance(data, dict):
            value = data.get("tag") or data.get("tags")
            rows = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
        elif isinstance(data, list):
            rows = data
        wanted_set = set(wanted)
        out = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = normalize_tag(row.get("name") or row.get("tag") or "")
            if name in wanted_set:
                out[name] = group_from_tag_type(row.get("type", row.get("category")))
        return out

    def _store_categories(self, mapping: dict, method: str = "dapi_tag_api") -> None:
        with self.ctx.tag_categories_lock:
            memo = self.ctx.tag_categories.setdefault(self.host, {})
            if method == "post_tag_info" and all(memo.get(k) == v for k, v in mapping.items()):
                return                          # every post brings its categories: write only what is new
            memo.update(mapping)
        try:
            from core.database.storage import upsert_tag_category_cache
            upsert_tag_category_cache(self.ctx.settings, self.host, mapping, method=method)
        except Exception as exc:
            self.ctx.log(f"    {self.label}: не удалось сохранить кэш категорий: {exc}")
