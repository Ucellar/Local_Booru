"""Danbooru 2 API: danbooru.donmai.us, booru.allthefallen.moe (ATF), aibooru, lolibooru."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from urllib.parse import urljoin

from core import site_agent
from core.parser.models import Post
from core.parser.sites.base import Site, SiteUnavailable, TextBody
from core.parser.util import norm_md5, post_dicts, short
from core.site_posts import fields as post_fields

_SPLIT_FIELDS = {
    "artist": ["tag_string_artist"],
    "character": ["tag_string_character"],
    "copyright": ["tag_string_copyright"],
    "general": ["tag_string_general"],
    "meta": ["tag_string_meta"],
}

# ATF answers with a JavaScript proof-of-work page until the challenge cookie
# is set.  One solve is shared by every worker thread for ten minutes.
_ATF_LOCK = threading.Lock()
_ATF_COOKIES: dict[str, tuple[float, list]] = {}


class DanbooruSite(Site):
    engine = "danbooru"
    post_path = "/posts/{id}"
    md5_batch_limit = 50

    def __init__(self, domain, cfg, ctx):
        super().__init__(domain, cfg, ctx)
        if self.host in ("danbooru.donmai.us", "donmai.us"):
            self.host = "danbooru.donmai.us"
            self.aliases = ("donmai.us", "safebooru.donmai.us")
            self.root = "https://danbooru.donmai.us"
        self.is_atf = "allthefallen" in self.host

    # ── auth/headers ────────────────────────────────────────────────────────
    def _headers(self) -> dict:
        return {"Accept": "application/json", "User-Agent": self.api_user_agent()}

    def api_user_agent(self) -> str:
        """Danbooru's rules (its help:api): «a unique User-Agent header that contains your user ID … Don't impersonate
        browsers» — the program's name, the login and the user's number (core/site_agent.py, v5.44.24).  ATF and the
        other sites of this engine: as before."""
        if not site_agent.is_own(self.host):
            return super().api_user_agent()
        custom = str(self.cfg.get("user_agent") or self.cfg.get("api_user_agent") or "")
        return site_agent.user_agent(self.host, self.ctx.settings, name=self.login or None,
                                     user_id=self.user_id if self.user_id.isdigit() else None, custom=custom)

    def preflight(self) -> None:
        """Danbooru: its rules ask for the user's number in every request.  The «User ID» of «Сайты и ключи» is it;
        not written, with the login and the API key — Danbooru's ``/profile.json`` tells it, asked once per start of
        the program; neither — said once in the journal (the requests go on without it)."""
        if not site_agent.is_own(self.host) or self.user_id.isdigit():
            return
        if site_agent.learned_user_id("danbooru", self.login):
            return
        if not (self.login and self.api_key):
            self.ctx.log(f"  {self.host}: в «Сайтах и ключах» не вписан номер пользователя Danbooru («User ID») — "
                         "правила Danbooru просят его в запросах программы (он на странице профиля; или впишите логин "
                         "и API-ключ — номер программа спросит сама)")
            return
        if not site_agent.first_try("danbooru", self.login):
            return
        try:
            data = self.get_json(f"{self.root}/profile.json", headers=self._headers(), auth=self._auth(), soft=True)
        except SiteUnavailable as exc:
            self.ctx.log(f"  {self.host}: номер пользователя Danbooru не узнан ({short(str(exc), 80)}) — запросы идут "
                         "с логином")
            return
        number = str((data or {}).get("id") or "") if isinstance(data, dict) else ""
        if number.isdigit():
            site_agent.remember_user_id("danbooru", self.login, number)
            self.ctx.log(f"  {self.host}: номер пользователя Danbooru #{number} — в подписи запросов программы "
                         "(правила Danbooru)")

    def _auth(self):
        return (self.login, self.api_key) if self.login and self.api_key else None

    def _api(self, url: str, params=None, soft: bool = False):
        if self.is_atf:
            self._atf_apply_shared_cookies()
        data = self.get_json(url, params=params, headers=self._headers(), auth=self._auth(),
                             soft=soft, accept_text=self.is_atf)
        if isinstance(data, TextBody):
            if not _is_atf_challenge(data):
                raise SiteUnavailable("ответ не JSON: " + short(data, 80))
            self._atf_unlock(url, str(data))
            data = self.get_json(url, params=params, headers=self._headers(), auth=self._auth(),
                                 soft=soft, accept_text=True)
            if isinstance(data, TextBody):
                self.disable("после проверки ATF всё ещё показывает страницу проверки")
                raise SiteUnavailable("страница проверки ATF", transient=False)
        return data

    # ── interface ───────────────────────────────────────────────────────────

    def search_md5(self, md5: str) -> Post | None:
        data = self._api(f"{self.root}/posts.json", {"tags": f"md5:{md5}", "limit": 1})
        for raw in post_dicts(data):
            remote = norm_md5(raw.get("md5"))
            if remote and remote != md5:
                self.ctx.log(f"    {self.label}: пост {raw.get('id')} с другим MD5 ({remote}) отброшен")
                continue
            # Gold-only/restricted posts hide md5 and file_url, but the exact
            # md5: metatag already identifies them, so they are still a match.
            post = self.parse(raw)
            if post and post.tags:
                if not post.md5:
                    post.md5 = md5
                return post
        return None

    def search_md5_many(self, md5s: list, style: str = "", probe: bool = False) -> tuple[dict, bool]:
        data = self._api(f"{self.root}/posts.json", {"tags": "md5:" + ",".join(md5s), "limit": len(md5s)})
        return self._map_md5_answer(post_dicts(data), md5s, lambda raw: raw.get("md5"), self.parse)

    id_batch_limit = 100

    def id_batch_styles(self) -> tuple:
        return ("list",)

    def posts_by_ids(self, ids: list, style: str = "") -> list:
        query = "id:" + ",".join(str(i) for i in ids)
        return post_dicts(self._api(f"{self.root}/posts.json", {"tags": query, "limit": max(1, len(ids))},
                                    soft=len(ids) > 1))

    def fetch_post(self, post_id: str) -> Post | None:
        if not str(post_id).isdigit():
            return None
        data = self._api(f"{self.root}/posts/{post_id}.json")
        if isinstance(data, dict):
            raw = data.get("post") if isinstance(data.get("post"), dict) else data
            return self.parse(raw)
        return None

    def md5s_by_pixel_hash(self, pixel_hash: str, limit: int = 5) -> list:
        """Original files (their MD5) whose pixels are exactly these: a copy saved without its
        metadata or re-compressed losslessly has another MD5 but the same pixel_hash.  (v5.35.1) An answer with
        pictures of other pixels: the site does not know the search (it shows its newest pictures) — it refuses,
        else those pictures would be taken for the file's original."""
        wanted = norm_md5(pixel_hash)
        data = self._api(f"{self.root}/media_assets.json",
                         {"search[pixel_hash]": pixel_hash, "limit": max(1, min(20, limit))}, soft=True)
        rows = _asset_rows(data)
        if any(norm_md5(r.get("pixel_hash")) not in ("", wanted) for r in rows):
            raise SiteUnavailable("на поиск по pixel_hash ответил чужими картинками", transient=False)
        return list(dict.fromkeys(m for m in (norm_md5(r.get("md5")) for r in rows) if m))

    def newest_pixel_hashes(self, count: int = 3) -> list:
        """The pixel_hash of the site's newest pictures it shows whole (their MD5 too; active, public) — ones it surely
        has, up to ``count`` ([] — its answer tells none): the proof that it understands a list of pixel hashes
        (``core/parser/pixels.py``).  (v5.35.1) Several: the newest one may be an upload still in work, which a search
        does not show."""
        data = self._api(f"{self.root}/media_assets.json", {"limit": 20}, soft=True)
        hashes: list = []
        for row in _asset_rows(data):
            pixel_hash = norm_md5(row.get("pixel_hash"))
            if not pixel_hash or pixel_hash in hashes or not norm_md5(row.get("md5")):
                continue
            if str(row.get("status") or "active") != "active" or row.get("is_public") is False:
                continue
            hashes.append(pixel_hash)
        return hashes[:max(1, int(count))]

    def md5s_by_pixel_hashes(self, pixel_hashes: list, limit_each: int = 5):
        """Several pixel hashes in one request: ({pixel hash: [the originals' MD5]}, {the pixel hashes the answer
        had}); None — it had an asset nobody asked for (a site that does not know the list answers with its newest
        assets).  (v5.35.2) The list as ``search[pixel_hash][]=a&search[pixel_hash][]=b``: Danbooru keeps pixel_hash
        as a uuid, and a uuid column takes no ``_comma`` list (searchable.rb: «search_uuid_attribute» — only the bare
        name, ``_eq``, ``_not_eq``); the bare name with several values is a ``WHERE … IN``.  5.34 asked
        ``search[pixel_hash_comma]``: both Danbooru and ATF answered it with their newest pictures."""
        wanted = list(dict.fromkeys(norm_md5(h) for h in pixel_hashes if norm_md5(h)))
        data = self._api(f"{self.root}/media_assets.json",
                         {"search[pixel_hash][]": wanted,
                          "limit": max(2, min(200, len(wanted) * max(1, int(limit_each))))}, soft=True)
        found: dict = {h: [] for h in wanted}
        seen: set = set()
        for row in _asset_rows(data):
            pixel_hash = norm_md5(row.get("pixel_hash"))
            if pixel_hash not in found:
                return None
            seen.add(pixel_hash)
            md5 = norm_md5(row.get("md5"))
            if md5 and md5 not in found[pixel_hash]:
                found[pixel_hash].append(md5)
        return found, seen

    def search_query(self, query: str, limit: int = 3) -> list:
        data = self._api(f"{self.root}/posts.json", {"tags": query, "limit": max(1, min(20, limit))}, soft=True)
        return [p for p in (self.parse(raw) for raw in post_dicts(data)) if p and p.tags]

    tag_page_max = 200
    can_search_tags = True

    def search_tags(self, query: str, page: int = 0, limit: int = 40) -> list:
        data = self._api(f"{self.root}/posts.json",
                         {"tags": query, "limit": max(1, min(self.tag_page_max, limit)), "page": int(page) + 1})
        return [(post, raw) for raw in post_dicts(data) for post in [self.parse(raw)] if post]

    def parse(self, raw: dict) -> Post | None:
        if not isinstance(raw, dict) or not raw.get("id"):
            return None
        groups = self.groups_from_fields(raw, _SPLIT_FIELDS)
        if not self.has_tags(groups):
            groups = self.flat_groups(str(raw.get("tag_string") or raw.get("tags") or "").split())
        post_id = str(raw.get("id"))
        sources = [raw["source"]] if isinstance(raw.get("source"), str) and raw.get("source") else []
        return Post(
            site=self.host, label=self.label, post_id=post_id, url=self.post_url(post_id),
            md5=norm_md5(raw.get("md5")), groups=groups, rating=str(raw.get("rating") or ""),
            sources=sources,
            preview_url=str(raw.get("preview_file_url") or raw.get("preview_url") or ""),
            file_url=str(raw.get("file_url") or raw.get("large_file_url") or ""),
            meta=post_fields(raw, self.engine),
        )

    # ── ATF verification gate ──────────────────────────────────────────────
    def _atf_apply_shared_cookies(self) -> None:
        shared = _ATF_COOKIES.get(self.host)
        if not shared or time.time() - shared[0] > 600:
            return
        sess = self.http.session(self.host)
        if getattr(sess, "_atf_cookie_stamp", 0) == shared[0]:
            return
        for name, value, domain, path in shared[1]:
            sess.cookies.set(name, value, domain=domain, path=path)
        sess._atf_cookie_stamp = shared[0]

    def _atf_unlock(self, url: str, page: str) -> None:
        sess = self.http.session(self.host)
        with _ATF_LOCK:
            shared = _ATF_COOKIES.get(self.host)
            if shared and time.time() - shared[0] < 600 and getattr(sess, "_atf_cookie_stamp", 0) != shared[0]:
                self._atf_apply_shared_cookies()   # another worker already solved it
                return
            self.ctx.log(f"    {self.label}: страница проверки ATF — решаю proof-of-work")
            if not self._atf_solve(url, page):
                # The page format changed or the answer was refused: it will fail for every
                # file, so switch ATF off for this run instead of deferring everything.
                self.disable("не удалось пройти проверку ATF (страница проверки изменилась?)")
                raise SiteUnavailable("проверка ATF не пройдена", transient=False)
            stamp = time.time()
            _ATF_COOKIES[self.host] = (stamp, [(c.name, c.value, c.domain, c.path) for c in sess.cookies
                                               if "allthefallen" in str(c.domain or "")])
            sess._atf_cookie_stamp = stamp

    def _atf_solve(self, url: str, page: str) -> bool:
        def const(name: str) -> str:
            m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*[\"']([^\"']*)[\"']", page)
            return m.group(1) if m else ""

        challenge_id = const("challenge_id")
        generated = const("challenge_generated")
        expires = const("challenge_cookie_expires")
        seed = const("powSeed")
        post_to = const("post_to") or self.host
        m = re.search(r"powPrefix\s*=\s*[\"']0[\"']\.repeat\((\d+)\)", page)
        prefix = "0" * (int(m.group(1)) if m else 5)
        m = re.search(r"const\s+delay\s*=\s*(\d+)", page)
        delay = int(m.group(1)) if m else 5
        if not (challenge_id and generated and expires and seed):
            self.ctx.log(f"    {self.label}: в странице проверки нет нужных полей")
            return False
        nonce, digest = "", ""
        for n in range(20_000_000):
            h = hashlib.sha1(f"{seed}:{n}".encode()).hexdigest()
            if h.startswith(prefix):
                nonce, digest = str(n), h
                break
            if n % 200_000 == 0:
                self.http.check_cancel()
        if not nonce:
            return False
        self.http.sleep(min(delay, 10) + 0.25)
        target = f"https://{self.host}/" if post_to.strip().lower() == self.host else urljoin(url, post_to)
        response = self.http.post(target, json={
            "challenge_id": challenge_id, "challenge_generated": generated,
            "challenge_cookie_expires": expires, "pow_nonce": nonce, "pow_hash": digest,
        }, headers={
            "Accept": "*/*", "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest",
            "X-Verification-Challenge": "1", "Referer": url, "Origin": f"https://{self.host}",
        }, attempts=1)
        self.ctx.log(f"    {self.label}: проверка ATF → HTTP {response.status_code} {short(response.text, 60)}")
        return response.status_code == 200


def _is_atf_challenge(text: str) -> bool:
    head = (text or "")[:20000].lower()
    return ("| verification" in head and "allthefallen" in head) or "x-verification-challenge" in head \
        or "powseed" in head or "challenge-checkbox" in head


def _asset_rows(data) -> list:
    """The media assets of an answer of ``media_assets.json`` (a list, or ``{"media_assets": [...]}``)."""
    rows = data if isinstance(data, list) else (data.get("media_assets") or []) if isinstance(data, dict) else []
    return [r for r in rows if isinstance(r, dict)]
