"""Moebooru API: yande.re, konachan.com, konachan.net."""
from __future__ import annotations

from core.parser.models import Post
from core.parser.sites.base import Site
from core.parser.util import norm_md5, post_dicts
from core.site_posts import fields as post_fields
from core.tagger.tag_groups import add_tags_to_groups, empty_tag_groups, group_from_tag_type, normalize_tag

_EXTRA = {"circle": "artist", "faults": "meta", "style": "meta"}


class MoebooruSite(Site):
    engine = "moebooru"
    post_path = "/post/show/{id}"
    md5_batch_limit = 50


    def _query(self, tags: str, limit: int = 1, soft: bool = False, page: int = 0) -> list:
        # api_version=2 + include_tags=1 adds {"tags": {name: type}} for categories.
        params = {"tags": tags, "limit": limit, "api_version": 2, "include_tags": 1}
        if page:
            params["page"] = int(page) + 1
        data = self.get_json(f"{self.root}/post.json", params=params, headers={"Accept": "application/json"}, soft=soft)
        types = data.get("tags") if isinstance(data, dict) and isinstance(data.get("tags"), dict) else {}
        return [(raw, types) for raw in post_dicts(data, keys=("posts",))]

    def search_md5(self, md5: str) -> Post | None:
        for raw, types in self._query(f"md5:{md5}"):
            if norm_md5(raw.get("md5")) == md5:
                return self.parse(raw, types)
        return None

    def search_md5_many(self, md5s: list, style: str = "", probe: bool = False) -> tuple[dict, bool]:
        rows = self._query("md5:" + ",".join(md5s), limit=len(md5s))
        return self._map_md5_answer([raw for raw, _types in rows], md5s, lambda raw: raw.get("md5"),
                                    lambda raw: self.parse(raw, rows[0][1] if rows else {}))

    def fetch_post(self, post_id: str) -> Post | None:
        if not str(post_id).isdigit():
            return None
        rows = self._query(f"id:{post_id}")
        return self.parse(*rows[0]) if rows else None

    id_batch_limit = 40

    def id_batch_styles(self) -> tuple:
        return ("list",)

    def posts_by_ids(self, ids: list, style: str = "") -> list:
        query = "id:" + ",".join(str(i) for i in ids)
        return [raw for raw, _types in self._query(query, max(1, len(ids)), soft=len(ids) > 1)]

    def search_query(self, query: str, limit: int = 3) -> list:
        return [p for p in (self.parse(raw, types) for raw, types in self._query(query, limit, soft=True)) if p and p.tags]

    can_search_tags = True

    def search_tags(self, query: str, page: int = 0, limit: int = 40) -> list:
        rows = self._query(query, max(1, min(self.tag_page_max, limit)), page=page)
        return [(post, raw) for raw, types in rows for post in [self.parse(raw, types)] if post]

    def parse(self, raw: dict, types: dict | None = None) -> Post | None:
        if not isinstance(raw, dict) or not raw.get("id"):
            return None
        tags = [normalize_tag(t) for t in str(raw.get("tags") or "").split() if normalize_tag(t)]
        if not tags:
            return None
        groups = empty_tag_groups()
        for tag in tags:
            kind = str((types or {}).get(tag, "general")).lower()
            add_tags_to_groups(groups, _EXTRA.get(kind) or group_from_tag_type(kind), [tag])
        post_id = str(raw.get("id"))
        source = str(raw.get("source") or "").strip()
        return Post(
            site=self.host, label=self.label, post_id=post_id, url=self.post_url(post_id),
            md5=norm_md5(raw.get("md5")), groups=groups, rating=str(raw.get("rating") or ""),
            sources=[source] if source.startswith("http") else [],
            preview_url=str(raw.get("preview_url") or raw.get("sample_url") or ""),
            file_url=str(raw.get("file_url") or ""),
            meta=post_fields(raw, self.engine),
        )
