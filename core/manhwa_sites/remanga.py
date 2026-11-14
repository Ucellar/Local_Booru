"""(v5.44.168) Remanga and ReComics (its engine) — of the sites of ``core/manhwa_sources.py``: their open API: the
title, its translators' branches and their chapters, a chapter's pages.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before (``manhwa_sources.Remanga``).
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urljoin, urlparse

from core import manhwa_sources as S


class Remanga(S.Source):
    name = "remanga"
    feed_order, catalog_order, legacy_search_order = 10, 10, 10
    catalog_kinds = ("manga", "manhwa")
    catalog_filters = True
    label = "Remanga"
    hosts = ("remanga.org",)
    address_value, api_value = "REMANGA_SITE", "REMANGA_API"
    cover_hosts = ("remanga.org",)

    def site(self) -> str:
        return S.address(self.name) or S.REMANGA_SITE

    def api(self) -> str:
        return S.address(self.name + "_api") or S._api_of(S.address(self.name)) or S.REMANGA_API

    def key_of(self, url: str) -> str:
        if not S._ours(url, self.site(), self.hosts):
            return ""
        m = re.match(r"/manga/([^/?#]+)", urlparse(url).path)
        return m.group(1) if m else ""

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        # (v5.44.62) a dir may hold «<>» (Remanga's «<29.04.2026>omniscient-reader_»): in the address it is quoted
        data = net.json(f"{self.api()}/api/v2/titles/{quote(unquote(key), safe='')}/", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        c = data.get("content", data) if isinstance(data, dict) else {}
        if not isinstance(c, dict) or not (c.get("main_name") or c.get("secondary_name")):
            raise S.Missing(f"{self.label} не знает такого тайтла")
        others = [x.strip() for x in str(c.get("another_name") or "").split(" / ") if x.strip()]
        alt = [x for x in [str(c.get("secondary_name") or "").strip(), *others] if x and x != c.get("main_name")]
        cover = c.get("cover") or {}
        cover_path = (cover.get("high") or cover.get("mid") or cover.get("low") or "") if isinstance(cover, dict) else ""
        branches = []
        for b in c.get("branches") or []:
            if isinstance(b, dict) and b.get("id") is not None:
                branches.append(S.Branch(id=str(b["id"]), name=", ".join(S._names(b.get("publishers"))),
                                       votes=S._int(b.get("total_votes")), count=S._int(b.get("count_chapters"))))
        age = c.get("age_limit") if isinstance(c.get("age_limit"), dict) else {}
        dir_ = str(c.get("dir") or unquote(key))
        status = str((c.get("status") or {}).get("name") or "") if isinstance(c.get("status"), dict) else ""
        return S.checked(S.TitleRef(                       # (v5.44.143) the program's gate — by a link too
            site=self.name, key=dir_, url=f"{self.site()}/manga/{quote(dir_, safe='')}",
            title=str(c.get("main_name") or c.get("secondary_name")).strip(), alt_titles=alt,
            type=str((c.get("type") or {}).get("name") or "") if isinstance(c.get("type"), dict) else "",
            status=status, licensed=bool(c.get("is_licensed")) or status.casefold().startswith("лицензир"),
            year=S._int(c.get("issue_year")), age=str(age.get("name") or ""), is_erotic=bool(c.get("is_erotic")),
            genres=S._names(c.get("genres")), tags=S._names(c.get("categories")), description=S.plain(c.get("description")),
            cover_url=urljoin(self.site() + "/", cover_path) if cover_path else "",
            authors=[x["name"] for x in c.get("creators") or [] if isinstance(x, dict) and x.get("name")
                     and x.get("type") == 1],
            branches=branches))

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        branch = branch or (title.best_branch().id if title.best_branch() else "")
        if not branch:
            return []
        out, page = [], 1
        while page and page <= 200:
            data = net.json(f"{self.api()}/api/v2/titles/chapters/?branch_id={branch}&ordering=index&page={page}"
                            f"&count=40", what=self.label, headers={"Accept": "application/json",
                                                                   "Referer": self.site() + "/"})
            items = data.get("results") if isinstance(data, dict) else None
            if items is None and isinstance(data, dict):
                items = data.get("content")
            for x in items or []:
                if not isinstance(x, dict) or x.get("id") is None:
                    continue
                if x.get("is_published") is False:
                    continue                                   # announced, not out yet
                paid = bool(x.get("is_paid")) and not bool(x.get("is_bought"))
                out.append(S.ChapterRef(id=str(x["id"]), tome=S._int(x.get("tome")), number=S._num(x.get("chapter")),
                                      name=str(x.get("name") or "").strip(), free=not paid,
                                      free_at=str(x.get("pub_date") or "") if paid else "",
                                      date=str(x.get("upload_date") or x.get("pub_date") or ""),
                                      url=f"{title.url}/{x['id']}", index=S._int(x.get("index"))))
            page = page + 1 if isinstance(data, dict) and data.get("next") else 0
        out.sort(key=lambda ch: (ch.index, ch.tome, S._number_key(ch.number)))
        return out

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        if not chapter.free:
            raise S.Paid("глава платная — её программа не скачивает")
        data = net.json(f"{self.api()}/api/v2/titles/chapters/{chapter.id}/", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        c = data.get("content", data) if isinstance(data, dict) else {}
        if not isinstance(c, dict):
            raise S.SourceError(f"{self.label} не дал главу")
        if c.get("is_paid") and not c.get("is_bought"):
            raise S.Paid("глава платная — её программа не скачивает")
        out = []
        for group in c.get("pages") or []:
            for p in (group if isinstance(group, list) else [group]):
                if isinstance(p, dict) and p.get("link"):
                    out.append(S.Picture(url=str(p["link"]), width=S._int(p.get("width")), height=S._int(p.get("height"))))
        if not out:
            raise S.SourceError(f"в главе на {self.label} нет страниц")
        return out

    def search(self, net: S.Net, query: str) -> list:
        data = net.json(f"{self.api()}/api/v2/search/?query={quote(query)}", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        return self._hits(data)

    FEED_ORDER = {"new": "-chapter_date", "popular": "-rating"}
    FEED_COUNT = 30

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        """(v5.44.83) Its catalog — ``/api/v2/search/catalog/`` (the old ``/api/search/catalog/`` when it is not)."""
        query = f"ordering={self.FEED_ORDER.get(how, '-chapter_date')}&count={self.FEED_COUNT}&page={max(1, page)}"
        headers = {"Accept": "application/json", "Referer": self.site() + "/"}
        try:
            data = net.json(f"{self.api()}/api/v2/search/catalog/?{query}", what=self.label, headers=headers)
        except S.Missing:
            data = net.json(f"{self.api()}/api/search/catalog/?{query}", what=self.label, headers=headers)
        hits = self._hits(data)
        if isinstance(data, dict) and "next" in data:
            return hits, bool(data.get("next"))
        props = data.get("props") if isinstance(data, dict) and isinstance(data.get("props"), dict) else {}
        if props.get("total_pages"):
            return hits, S._int(props.get("page") or page) < S._int(props.get("total_pages"))
        return hits, len(hits) >= self.FEED_COUNT

    def _hits(self, data) -> list:
        items = data.get("results") if isinstance(data, dict) else None
        if items is None and isinstance(data, dict):
            items = data.get("content")
        out = []
        for x in items or []:
            if not isinstance(x, dict) or not x.get("dir"):
                continue
            names = [x.get("main_name"), x.get("secondary_name"), *str(x.get("another_name") or "").split(" / ")]
            cover = x.get("cover") if isinstance(x.get("cover"), dict) else {}
            cover_path = str(cover.get("mid") or cover.get("low") or cover.get("high") or "")
            dir_ = str(x["dir"])
            out.append(S.Hit(site=self.name, key=dir_, names=[str(n).strip() for n in names if n and str(n).strip()],
                           type=str((x.get("type") or {}).get("name") or "") if isinstance(x.get("type"), dict) else "",
                           year=S._int(x.get("issue_year")), url=f"{self.site()}/manga/{quote(dir_, safe='')}",
                           cover=urljoin(self.site() + "/", cover_path) if cover_path else "",
                           chapters=S._int(x.get("count_chapters")), rating=S._float(x.get("avg_rating")),
                           votes=S._int(x.get("total_votes")), adult=bool(x.get("is_erotic"))))
        return out

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"


class ReComics(Remanga):
    """(v5.44.61) recomics.org — Remanga's sister site (Russian comics, «Рукомикс»): the same API v2 at its own address;
    its title comes without the «content» wrapper (read either way)."""
    name = "recomics"
    feed_order, catalog_order, legacy_search_order = 0, 0, 20
    catalog_kinds = ()
    catalog_filters = False
    label = "ReComics"
    hosts = ("recomics.org",)
    address_value, api_value = "RECOMICS_SITE", "RECOMICS_API"
    cover_hosts = ("recomics.org",)

    def site(self) -> str:
        return S.address(self.name) or S.RECOMICS_SITE

    def api(self) -> str:
        return S.address(self.name + "_api") or S._api_of(S.address(self.name)) or S.RECOMICS_API
