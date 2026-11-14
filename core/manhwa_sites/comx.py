"""(v5.44.168) com-x.life — of the sites of ``core/manhwa_sources.py``: its pages behind its own check for robots, read
by ``core/manhwa_comx.py``.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before (``manhwa_sources.ComX``).
"""
from __future__ import annotations

import re
from urllib.parse import quote, urljoin, urlparse

from core import manhwa_sources as S


class ComX(S.Source):
    """(v5.44.72) com-x.life — every page of it is a check for robots first (looked at from the owner's computer,
    5.11.2026); (v5.44.73) the program window's browser gets in (``Net`` — ``core/site_browser.py``), and its pages are
    read by ``core/manhwa_comx.py``: the title's page — its names, description, poster, facts and ``window.__DATA__``
    chapters; the reader — the pictures; the search of DataLife Engine.  Its chapters are all free; it has no
    translators to choose (one branch)."""
    name = "comx"
    label = "com-x.life"
    hosts = ("com-x.life",)
    address_value = "COMX_SITE"
    by_name = False                   # (v5.44.75) its search goes through the window's browser: only by a link

    def site(self) -> str:
        return S.address(self.name) or S.COMX_SITE

    def key_of(self, url: str) -> str:
        if not S._ours(url, self.site(), self.hosts):
            return ""
        m = re.fullmatch(r"/(\d+)-([a-z0-9][a-z0-9-]*)\.html?", urlparse(url).path, re.I)
        return f"{m.group(1)}-{m.group(2).lower()}" if m else ""

    def _page(self, net: S.Net, url: str, referer: str = "") -> str:
        return net.text(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        from core import manhwa_comx as X
        url = f"{self.site()}/{key}.html"
        page = self._page(net, url)
        kept = X.keep("title", page)
        data = X.data_of(page)
        names = X.names_of(page)
        if not names:
            raise S.SourceError("на странице com-x.life нет тайтла" + (f" (она сохранена: {kept})" if kept else ""))
        news_id = str(data.get("news_id") or key.split("-", 1)[0])
        raw = data.get("chapters") if isinstance(data.get("chapters"), list) else X.chapters_in_html(page, news_id)
        net.kept[("comx", key)] = (news_id, raw)
        facts = X.facts_of(page)
        cover = X.meta(page, "og:image")
        genres = facts["genres"]
        return S.checked(S.TitleRef(site=self.name, key=key, url=url, title=names[0], alt_titles=names[1:],   # (v5.44.143)
                              type=facts["type"], status=facts["status"], year=facts["year"], genres=genres,
                              is_erotic=any(g.casefold() in ("хентай", "эротика", "hentai") for g in genres),
                              description=X.description_of(page), cover_url=urljoin(url, cover) if cover else "",
                              branches=[S.Branch(id="", name="com-x.life")], site_id=news_id))

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        from core import manhwa_comx as X
        news_id, raw = net.kept.get(("comx", title.key)) or (title.site_id, None)
        if raw is None:
            page = self._page(net, title.url)
            data = X.data_of(page)
            news_id = str(data.get("news_id") or news_id or title.key.split("-", 1)[0])
            raw = data.get("chapters") if isinstance(data.get("chapters"), list) else X.chapters_in_html(page, news_id)
        found: dict = {}
        for x in raw:
            if not isinstance(x, dict) or x.get("id") is None:
                continue
            tome, number, own = X.numbers_of(x.get("title") or x.get("name") or "", x.get("posi"))
            ch = S.ChapterRef(id=str(x["id"]), tome=tome, number=number, name=own, date=str(x.get("date") or ""),
                            url=f"{self.site()}/reader/{news_id}/{x['id']}")
            old = found.get((tome, number))                # one chapter uploaded twice: the later one
            if old is None or S._int(ch.id) > S._int(old.id):
                found[(tome, number)] = ch
        out = sorted(found.values(), key=lambda c: (c.tome, S._number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        from core import manhwa_comx as X
        page = self._page(net, chapter.url, referer=title.url)
        urls = X.picture_urls(page, X.data_of(page), chapter.url, S.COMX_IMAGES)
        if not urls:
            kept = X.keep("reader", page)
            if X.CLOSED_WORDS.search(X.text_of(page)):
                raise S.Paid("глава закрыта на com-x.life — программа такие не скачивает")
            raise S.SourceError("на странице главы com-x.life нет картинок" + (f" (она сохранена: {kept})" if kept else ""))
        return [S.Picture(url=u) for u in urls]

    def search(self, net: S.Net, query: str) -> list:
        """DataLife Engine's search: its form (POST), else the same by the address (GET)."""
        from core import manhwa_comx as X
        form = {"do": "search", "subaction": "search", "story": query, "search_start": "0", "full_search": "0",
                "result_from": "1"}
        try:
            body, _ctype = net.fetch(f"{self.site()}/index.php?do=search", method="POST", data=form, what=self.label,
                                     headers={"Referer": self.site() + "/"})
            page = body.decode("utf-8", "replace")
        except (S.Closed, S.Refused, S.Missing):
            page = self._page(net, f"{self.site()}/index.php?do=search&subaction=search&story={quote(query)}")
        X.keep("search", page)
        return [S.Hit(site=self.name, key=key, names=names) for key, names in X.hits_of(page)]

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"
