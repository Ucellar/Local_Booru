"""e621 / e926 JSON API (tags already grouped by category)."""
from __future__ import annotations

from core import site_agent
from core.parser.models import Post
from core.parser.sites.base import Site
from core.parser.util import norm_md5, post_dicts
from core.site_posts import fields as post_fields
from core.tagger.tag_groups import add_tags_to_groups, empty_tag_groups

_GROUPS = {
    "general": "general", "artist": "artist", "contributor": "contributor", "copyright": "copyright",
    "character": "character", "species": "species", "meta": "meta", "lore": "lore", "invalid": "invalid",
}


class E621Site(Site):
    engine = "e621"
    post_path = "/posts/{id}"
    md5_batch_limit = 50

    def _headers(self) -> dict:
        return {"Accept": "application/json", "User-Agent": self.api_user_agent()}

    def api_user_agent(self) -> str:
        """e621's rules (its API help): the program's name and the person's login on e621 — never a browser's
        (core/site_agent.py, v5.44.22)."""
        custom = str(self.cfg.get("user_agent") or self.cfg.get("api_user_agent") or "")
        return site_agent.user_agent(self.host, self.ctx.settings, name=self.login or None, custom=custom)

    def preflight(self) -> None:
        """No login on e621 in «Сайты и ключи»: said once in the journal — e621 asks every program to carry the name
        of its person's account (to write to it when something goes wrong); the requests go on without it."""
        if not self.login and not site_agent.account(self.ctx.settings, site_agent.family(self.host))[0]:
            self.ctx.log(f"  {self.host}: в «Сайтах и ключах» не вписан логин e621 — правила e621 просят, чтобы в "
                         "запросах программы было имя аккаунта (чтобы написать, если что-то не так); API-ключ — по "
                         "желанию, с ним поиск по картинке на e621 быстрее")

    def _auth(self):
        return (self.login, self.api_key) if self.login and self.api_key else None


    def search_md5(self, md5: str) -> Post | None:
        answered = self._from_export([md5])
        if answered is not None:
            return answered.get(md5)
        data = self.get_json(f"{self.root}/posts.json", params={"tags": f"md5:{md5}", "limit": 1},
                             headers=self._headers(), auth=self._auth())
        for raw in post_dicts(data, keys=("posts",)):
            if norm_md5((raw.get("file") or {}).get("md5")) == md5:
                return self.parse(raw)
        return None

    def search_md5_many(self, md5s: list, style: str = "", probe: bool = False) -> tuple[dict, bool]:
        answered = self._from_export(md5s)
        if answered is not None:
            return answered, True
        return self._md5_api(md5s)

    def _md5_api(self, md5s: list) -> tuple[dict, bool]:
        data = self.get_json(f"{self.root}/posts.json", params={"tags": "md5:" + ",".join(md5s), "limit": len(md5s)},
                             headers=self._headers(), auth=self._auth())
        return self._map_md5_answer(post_dicts(data, keys=("posts",)), md5s,
                                    lambda raw: (raw.get("file") or {}).get("md5"), self.parse)

    def _from_export(self, md5s: list) -> dict | None:
        """(v5.44.25) The MD5s answered from e621's export (core/e621_export.py; e621's rules: «tens of thousands of
        lookups» — its exports, not the API), or None — it cannot answer (off, not made yet).  A post it knows is
        asked by its number (``posts_by_ids``, a hundred in a request: its tags fresh); a deleted one is not on e621;
        what it does not know is not on e621 while it is current, else asked by MD5 as before."""
        from core import e621_export
        answer = e621_export.lookup(self.ctx.settings, md5s)
        if answer is None:
            return None
        if not getattr(self, "_export_said", False):
            self._export_said = True
            self.ctx.log(f"  {self.host}: MD5 — по выгрузке e621 от {e621_export.as_day(answer.export_date)} "
                         f"({e621_export.as_count(answer.rows)} постов); по API — посты, которые она знает"
                         + ("" if answer.current else ", и остальные, пока новые посты не догружены"))
        alive = {m: post for m, (post, flags) in answer.known.items() if not flags & e621_export.DELETED}
        found: dict = {}
        ids = sorted(set(alive.values()))
        raws: list = []
        for i in range(0, len(ids), max(1, self.id_batch_limit)):
            raws += self.posts_by_ids(ids[i:i + max(1, self.id_batch_limit)])
        if raws:
            got, _whole = self._map_md5_answer(raws, list(alive), lambda raw: (raw.get("file") or {}).get("md5"),
                                               self.parse)
            found.update(got)
        unknown = [m for m in md5s if m not in answer.known]
        if unknown and not answer.current:
            for i in range(0, len(unknown), max(1, self.md5_batch_limit)):
                got, _whole = self._md5_api(unknown[i:i + max(1, self.md5_batch_limit)])
                found.update(got)
        return found

    def fetch_post(self, post_id: str) -> Post | None:
        if not str(post_id).isdigit():
            return None
        data = self.get_json(f"{self.root}/posts/{post_id}.json", headers=self._headers(), auth=self._auth())
        raw = data.get("post") if isinstance(data, dict) else None
        return self.parse(raw) if isinstance(raw, dict) else None

    id_batch_limit = 100

    def id_batch_styles(self) -> tuple:
        return ("list",)

    def posts_by_ids(self, ids: list, style: str = "") -> list:
        data = self.get_json(f"{self.root}/posts.json",
                             params={"tags": "id:" + ",".join(str(i) for i in ids), "limit": max(1, len(ids))},
                             headers=self._headers(), auth=self._auth(), soft=len(ids) > 1)
        return post_dicts(data, keys=("posts",))

    def search_query(self, query: str, limit: int = 3) -> list:
        # soft: a relay query the site rejects (wildcards) must not switch the whole site off
        data = self.get_json(f"{self.root}/posts.json", params={"tags": query, "limit": max(1, min(20, limit))},
                             headers=self._headers(), auth=self._auth(), soft=True)
        return [p for p in (self.parse(raw) for raw in post_dicts(data, keys=("posts",))) if p and p.tags]

    tag_page_max = 320
    can_search_tags = True

    def search_tags(self, query: str, page: int = 0, limit: int = 40) -> list:
        data = self.get_json(f"{self.root}/posts.json",
                             params={"tags": query, "limit": max(1, min(self.tag_page_max, limit)), "page": int(page) + 1},
                             headers=self._headers(), auth=self._auth())
        return [(post, raw) for raw in post_dicts(data, keys=("posts",)) for post in [self.parse(raw)] if post]

    def parse(self, raw: dict) -> Post | None:
        if not isinstance(raw, dict) or not raw.get("id"):
            return None
        groups = empty_tag_groups()
        tag_map = raw.get("tags")
        if isinstance(tag_map, dict):
            for key, values in tag_map.items():
                if isinstance(values, list):
                    add_tags_to_groups(groups, _GROUPS.get(str(key).lower(), "general"), values)
        if not self.has_tags(groups):
            return None
        post_id = str(raw.get("id"))
        file_info = raw.get("file") or {}
        preview = (raw.get("preview") or {}).get("url") or (raw.get("sample") or {}).get("url") or ""
        return Post(
            site=self.host, label=self.label, post_id=post_id, url=self.post_url(post_id),
            md5=norm_md5(file_info.get("md5")), groups=groups, rating=str(raw.get("rating") or ""),
            sources=[s for s in raw.get("sources") or [] if isinstance(s, str) and s.startswith("http")],
            preview_url=str(preview or ""), file_url=str(file_info.get("url") or ""),
            meta=post_fields(raw, self.engine),
        )
