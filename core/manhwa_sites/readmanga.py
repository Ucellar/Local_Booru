"""(v5.44.168) ReadManga and AllHentai (the Grouple network) — of the sites of ``core/manhwa_sources.py``: their pages
and the search's JSON; a server that withholds a chapter's pictures — ``Withheld``.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before
(``manhwa_sources.ReadManga``).
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

from core import gates as G, manhwa_sources as S


class ReadManga(S.Source):
    """(v5.44.77) ReadManga (the Grouple network; it moves from address to address — now ``a.zazaza.me``, looked at from
    the owner's computer, 5.11.2026) — pages, not an API.  The title's page: ``h1.cr-hero-names__main``, the other
    names (``.cr-hero-names__alt`` spans, ``alternativeHeadline``), the description (``.cr-description__content``), the
    kind and the year (``.cr-hero-short-details``: ``/list/category/…``, ``/list/year/…``), the genres and the tags
    (``/list/genre/…``, ``/list/tag/…`` — «# Тег»), the authors (``.cr-main-person-item__name``), the status
    (``data-production-status``), the poster (``.cr-hero-overlay__bg`` ``data-bg``); the chapters — rows ``tr.item-row``
    (``data-num`` — the number ×10; ``a.chapter-link`` — ``/<title>/vol<N>/<M>``, its text «3 - 235 Имя»;
    ``data-date-raw``).  A chapter's pictures — ``/<title>/vol<N>/<M>?mtr=true`` (the site's own «мне есть 18»):
    ``rm_h.readerInit(chapterInfo, [[server, '', path, width, height, ''], …])``; a chapter taken down (a licence) —
    404 and ``/static/deleted…`` pictures: :class:`Paid` (another site's copy is the only way).  Its search —
    ``/search/suggestion?query=`` (JSON; only this site's titles: a link of its own, ``type`` MANGA).  One translation
    to a chapter; the list does not confirm that the reader still has its pages."""
    name = "readmanga"
    feed_order = 40
    label = "ReadManga"
    hosts = ("zazaza.me", "readmanga.live", "readmanga.io", "readmanga.me", "readmanga.ru", "readmanga.org")
    address_value = "READMANGA_SITE"
    cover_hosts = ("one-way.work", "zazaza.me", "readmanga.live", "readmanga.io")
    shown = "readmanga (a.zazaza.me)"
    chapter_access_unknown = True
    RESERVED = {"list", "search", "internal", "user", "users", "static", "news", "forum", "login", "register", "auth",
                "rss", "api", "help", "about", "top", "catalog", "random", "private", "ajax", "app", "uploads"}

    def site(self) -> str:
        return S.address(self.name) or S.READMANGA_SITE

    def key_of(self, url: str) -> str:
        if not S._host_of(url, self.hosts, self.site()):
            return ""
        m = re.match(r"/([a-z0-9][a-z0-9_]*)(?:/|$)", urlparse(url).path, re.I)
        if not m or m.group(1).lower() in self.RESERVED or not re.search(r"[a-z]", m.group(1), re.I):
            return ""
        return m.group(1)

    def _page(self, net: S.Net, url: str, referer: str = "") -> str:
        return net.text(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    @staticmethod
    def _links(page: str, kind: str, cls: str = "") -> list:
        """The words of the links to the site's lists of ``kind`` (``cls`` — only the links of that class: the
        title's own genres, not the ones of the menus and of the other titles on its page)."""
        out = []
        for attrs, inner in re.findall(r'<a\b([^>]*href="/list/' + kind + r'/[^"]*"[^>]*)>(.*?)</a>', page, re.S):
            if cls and not re.search(r'class="[^"]*\b' + re.escape(cls) + r'\b', attrs):
                continue
            x = S._text(inner).lstrip("#").strip()
            if x and x not in out:
                out.append(x)
        return out

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        url = f"{self.site()}/{key}"
        page = self._page(net, url)
        h1 = re.search(r'<h1[^>]*class="[^"]*cr-hero-names__main[^"]*"[^>]*>(.*?)</h1>', page, re.S) or \
            re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        name = S._text(h1.group(1)) if h1 else ""
        if not name:
            raise S.SourceError(f"на странице {self.label} нет тайтла")
        alt: list = []
        for found in [*re.findall(r'itemprop="alternativeHeadline"\s+content="([^"]*)"', page),
                      *[S._text(x) for x in re.findall(r"<span>([^<]+)</span>", S._block(page, "cr-hero-names__alt"))]]:
            for x in str(_html.unescape(found)).split(" / "):
                x = x.strip()
                if x and x != name and x not in alt:
                    alt.append(x)
        hero = S._block(page, "cr-hero-short-details")      # the kind and the year of the title, not of the menus
        kind = next((x for x in self._links(hero, "category") if x in S.KNOWN_TYPES), "")
        year = next((y for y in (S._year(x) for x in self._links(hero, "year")) if y), 0)
        genres = [g[:1].upper() + g[1:] for g in self._links(page, "genre", "creation-element-tags__item")]
        status = re.search(r'data-production-status="[^"]*"[^>]*>(.*?)<', page, re.S)
        poster = re.search(r'class="cr-hero-overlay__bg[^"]*"[^>]*data-bg="([^"]+)"', page) or \
            re.search(r'"(https?://[^"\s]+/uploads/pics/[^"\s]+_o\.(?:jpe?g|png|webp)[^"\s]*)"', page)
        authors = []
        for x in re.findall(r'class="cr-main-person-item__name[^"]*"[^>]*>(.*?)</span>', page, re.S):
            x = S._text(x)
            if x and x not in authors:
                authors.append(x)
        return S.checked(S.TitleRef(site=self.name, key=key, url=url, title=name, alt_titles=alt, type=kind,  # (v5.44.143)
                              status=S._text(status.group(1)) if status else "", year=year,
                              age=next(iter(self._links(page, "age_limitation")), ""),
                              is_erotic=any(g.casefold() in ("хентай", "эротика") for g in genres), genres=genres,
                              tags=[t[:1].upper() + t[1:] for t in self._links(page, "tag",
                                                                                "creation-element-tags__item")],
                              description=S.plain(S._block(page, "cr-description__content")),
                              cover_url=_html.unescape(poster.group(1)) if poster else "", authors=authors,
                              branches=[S.Branch(id="", name=self.label)]))

    _ROW = re.compile(r'<tr[^>]*class="[^"]*\bitem-row\b[^"]*"([^>]*)>(.*?)</tr>', re.S)

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        page = self._page(net, title.url)
        found: dict = {}
        for m in self._ROW.finditer(page):
            attrs, inner = m.group(1), m.group(2)
            link = re.search(r'<a[^>]*href="(/' + re.escape(title.key) + r'/vol(\d+)/([\d.]+))[^"]*"[^>]*>(.*?)</a>',
                             inner, re.S)
            if not link:
                continue
            tome, raw = S._int(link.group(2)), link.group(3)
            tenth = re.search(r'data-num="(\d+)"', attrs)
            number = S._num(f"{int(tenth.group(1)) // 10}.{int(tenth.group(1)) % 10}") if tenth else S._num(raw)
            own = re.sub(r"^\s*\d+\s*-\s*[\d.]+\s*", "", S._text(link.group(4)))
            own = re.sub(r"(?:^|\s)КОН$", "", own).strip()          # «КОН» — the title's last chapter
            date = re.search(r'data-date-raw="([^"]*)"', inner)
            found.setdefault((tome, number), S.ChapterRef(
                id=f"{tome}/{raw}", tome=tome, number=number, name=own,
                date=date.group(1).replace(" ", "T")[:19] if date else "", url=urljoin(self.site() + "/", link.group(1))))
        out = sorted(found.values(), key=lambda c: (c.tome, S._number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    _ITEM = re.compile(r"\[\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*\"([^\"]*)\"\s*,\s*(\d+)\s*,\s*(\d+)")
    GONE = "глава убрана с ReadManga"

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        def fetch(address: str) -> str:
            try:
                return self._page(net, address, referer=title.url)
            except S.Missing as exc:
                raise S.Paid(self.GONE) from exc
        return self._marked(title, chapter, fetch)

    @staticmethod
    def _address(url: str, mark: str) -> str:
        """A chapter's page as the site's reader asks it: «mtr=true» (its own «мне есть 18»); (v5.44.107) a signed-in
        reader's — with «d=<the account's mark>» too, as its script makes the chapters' links (``initUserHash``)."""
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ("d", "mtr")]
        if mark:
            query.append(("d", mark))
        query.append(("mtr", "true"))
        return urlunsplit(parts._replace(query=urlencode(query)))

    def _marked(self, title: S.TitleRef, chapter: S.ChapterRef, fetch) -> list:
        """(v5.44.107) The pictures of a chapter's page (``fetch`` — its text): with the account's mark once a page of
        the site named it; a page that shows none of its pictures but names the mark — asked again with it."""
        url = chapter.url or f"{title.url}/vol{chapter.id}"
        mark = S._ACCOUNT_MARKS.get(self.name, "")
        page = self._quietly(fetch, self._address(url, mark), mark)
        try:
            return self._reader(page)
        except S.SourceError:
            found = S._mark_in(page)
            if not found or found == mark:
                raise
        S._ACCOUNT_MARKS[self.name] = found
        return self._reader(self._quietly(fetch, self._address(url, found), found))

    @staticmethod
    def _quietly(fetch, address: str, mark: str) -> str:
        """``fetch(address)`` — words of a failure without the account's mark (they may reach the journal)."""
        try:
            return fetch(address)
        except S.SourceError as exc:
            if mark:
                exc.args = tuple(str(a).replace(mark, "…") for a in exc.args)
            raise

    def _reader(self, page: str) -> list:
        """(v5.44.99) The pictures of a chapter's page: ``rm_h.readerInit(…, [[server, '', path, w, h, ''], …])``."""
        start = max(page.find("rm_h.readerInit("), page.find("readerDoInit("))
        if start < 0:
            if re.search(r"(?i)войдите|авториз|только для зарегистрированных", S._text(page[:400_000])):
                raise S.Refused(f"главу {self.label} показывает только тем, кто вошёл на сайт")
            raise S.SourceError(f"на странице главы {self.label} нет картинок")
        out = []
        for server, middle, path, width, height in self._ITEM.findall(page[start:start + 600_000]):
            if not server and path.startswith("/static/"):
                raise S.Paid(self.GONE)
            link = server + middle + path
            out.append(S.Picture(url=link if link.startswith("http") else urljoin(self.site() + "/", link),
                               width=S._int(width), height=S._int(height)))
        if not out:
            raise S.SourceError(f"на странице главы {self.label} нет картинок")
        return out

    FEED_ORDER = {"new": "DATE_UPDATE", "popular": "RATING"}
    FEED_COUNT = 50

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        """Its catalog's API ``/api/catalog/search?offset=&sortType=`` (as Yomi asks it)."""
        data = net.json(f"{self.site()}/api/catalog/search?offset={self.FEED_COUNT * (max(1, page) - 1)}&sortType="
                        f"{self.FEED_ORDER.get(how, 'DATE_UPDATE')}", what=self.label,
                        headers={"Accept": "application/json, text/plain, */*", "Referer": self.site() + "/"})
        out = []
        for x in (data.get("list") if isinstance(data, dict) else None) or []:
            element = x.get("elementId") if isinstance(x, dict) and isinstance(x.get("elementId"), dict) else {}
            link = str(element.get("linkName") or "").strip("/")
            if not link or not re.fullmatch(r"[\w.-]{1,200}", link):
                continue
            kind = str((x.get("category") or {}).get("name") or "") if isinstance(x.get("category"), dict) else ""
            rating = x.get("rating") if isinstance(x.get("rating"), dict) else {}     # (v5.44.99) {rate, voteCount}
            out.append(S.Hit(site=self.name, key=link, names=[str(x.get("name") or link).strip()],
                           type=kind if kind in S.KNOWN_TYPES else "", url=f"{self.site()}/{link}",
                           cover=str(x.get("picUrl") or ""),
                           rating=round(S._float(x.get("rate") or rating.get("rate")), 1),
                           votes=S._int(rating.get("voteCount")), chapters=S._int(x.get("chaptersCount"))))
        total = S._int(data.get("total")) if isinstance(data, dict) else 0
        offset = S._int(data.get("offset")) if isinstance(data, dict) else 0
        limit = S._int(data.get("limit")) if isinstance(data, dict) else 0
        return out, (offset + (limit or self.FEED_COUNT) < total) if total else len(out) >= self.FEED_COUNT

    def search(self, net: S.Net, query: str) -> list:
        data = net.json(f"{self.site()}/search/suggestion?query={quote(query)}", what=self.label,
                        headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                 "X-Requested-With": "XMLHttpRequest", "Referer": self.site() + "/"})
        out = []
        for x in (data.get("suggestions") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict):
                continue
            link = str(x.get("link") or "")
            element = x.get("elementId") if isinstance(x.get("elementId"), dict) else {}
            if not link.startswith("/") or str(element.get("type") or "MANGA") != "MANGA":
                continue                               # another site of the network, a person, a character
            key = self.key_of(self.site() + link)
            if not key:
                continue
            names = [str(n).strip() for n in [x.get("value"), *(x.get("names") or [])] if n and str(n).strip()]
            kind = str(element.get("typeName") or "")
            out.append(S.Hit(site=self.name, key=key, names=list(dict.fromkeys(names)),
                           type=kind if kind in S.KNOWN_TYPES else "", year=S._int(x.get("year")),
                           url=f"{self.site()}/{key}", cover=str(x.get("thumbnail") or ""),
                           rating=round(S._float(x.get("rate")), 1)))
        return out

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"


class AllHentai(ReadManga):
    """(v5.44.99) AllHentai (``20.allhen.online``; the owner, 6.11.2026: «https://20.allhen.online/ добавить») — the
    hentai site of the Grouple network, the engine of ReadManga (looked at from the owner's computer 6.11.2026,
    ``_to_delete/cl96_research31–33.txt``): the same title's page (``cr-hero-…``; genres and tags — ``/list/genre/``,
    ``/list/tag/`` of the class ``creation-element-tags__item``), chapters (``tr.item-row``), reader
    (``rm_h.readerInit``), search (``/search/suggestion``), catalog API (``/api/catalog/search?offset=&sortType=``: 50 a
    page; ``DATE_UPDATE``, ``POPULARITY``, ``DATE_CREATE``, ``RATING``, ``NAME`` — an order it does not know is its
    default; a title's ``elements`` — its genres, tags, parodies and year, each with its number ``id``; ``category``
    — «Манга», «Манхва», «Комикс», «3D», «Без текста»…, ``rating`` {rate, voteCount}, ``picUrl`` on
    ``staticahen2.aaa200.one``; (v5.44.155) the API takes the filters — ``includeElementIds``, ``excludeElementIds``
    (the numbers of genres, tags, kinds: ``/api/catalog/elementsByType?type=2|40|3``), ``includeProductionStatuses``,
    ``years``, ``q``: ``core/catalog.py`` ``allhentai_url``).
    Without a sign-in a chapter's page answers 404 with every picture ``/static/deleted1.png`` (or with no reader): its
    chapters are read with the owner's sign-in — the site's cookies («Настройки → Манга и манхва»); (v5.44.107) and with
    the mark of his account in the chapter's address, as the site's own script adds it to the links
    (:meth:`ReadManga._marked`; without it a signed-in reader got the «deleted» pictures as well — the owner's «не
    скачалась … AllHentai не показал страницы главы», 7.11.2026).  Everything there is hentai; a title a gate closes
    (its names, genres, tags) is never shown, opened or taken."""
    name = "allhentai"
    feed_order, catalog_order = 80, 50
    catalog_kinds = ("manga", "manhwa")
    catalog_filters = True
    label = "AllHentai"
    hosts = ("allhen.online", "allhentai.ru", "allhentai.online")
    address_value = "ALLHENTAI_SITE"
    cover_hosts = ("aaa200.one", "allhen.online")   # (staticahen2.aaa200.one)
    shown = "allhentai (20.allhen.online)"
    adult_only = True
    SIGNED_ONLY = True
    chapter_access_unknown = True       # Its chapter list does not say whether the reader will give the pages.
    FEED_ORDER = {"new": "DATE_UPDATE", "popular": "POPULARITY"}
    GONE = "глава убрана с AllHentai: там её больше нет"
    SIGN_IN = ("AllHentai даёт читать и скачивать главы только со входом: войдите на сайте в своём браузере и добавьте "
               "его куки в «Настройки → Манга и манхва»")
    NOT_SHOWN = ("AllHentai не показал страницы главы: либо вход устарел (обновите куки в «Настройки → Манга и манхва»), "
                 "либо доступ к главе платный или ограничен, либо главу убрали с сайта")

    def site(self) -> str:
        return S.address(self.name) or S.ALLHENTAI_SITE

    def signed_in(self) -> bool:
        """The owner's cookies of the site (its address now, or any of its domains)."""
        host = (urlparse(self.site()).hostname or "").lower()
        return bool(S.signed_cookies(host)) or any(S.signed_cookies(h) for h in self.hosts)

    def sign_in_words(self) -> str:
        return self.SIGN_IN

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        ref = super().title(net, key)
        ref.is_erotic = True
        return S.checked(ref)

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        if not self.signed_in():
            raise S.Closed(self.sign_in_words())

        def fetch(address: str) -> str:
            body, ctype = net.fetch(address, what=self.label, keep_missing=True,
                                    headers={"Referer": title.url, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
            return S._decoded(body, ctype)
        try:
            return self._marked(title, chapter, fetch)       # (v5.44.107) with the mark of the owner's account
        except (S.Refused, S.Paid) as exc:
            raise S.Closed(self.NOT_SHOWN) from exc
        except S.Closed:
            raise
        except S.SourceError as exc:
            if "нет картинок" in str(exc):
                raise S.Closed(self.NOT_SHOWN) from exc
            raise

    def _never(self) -> str:
        """(v5.44.155) What its own lists are always asked without (``gates.left_out`` — «site»): the numbers of its
        catalog's elements."""
        left = G.left_out(self.name, "nsfw", where="site")
        return "".join(f"&excludeElementIds={n}" for n in dict.fromkeys((*left.tags, *left.genres)))

    @staticmethod
    def said(row) -> dict:
        """(v5.44.155) What a row of its catalog says of a title, for the program's gate (``gates.title``): the names
        of its ``elements`` (genres, tags, parodies, the year) and their numbers at the site — a genre's among the
        genres', every other one's among the tags'."""
        elements = [e for e in (row.get("elements") if isinstance(row, dict) else None) or [] if isinstance(e, dict)]
        return {"tags": [str(e.get("name") or "") for e in elements],
                "genre_ids": [e["id"] for e in elements if e.get("typeName") == "genre" and e.get("id") is not None],
                "tag_ids": [e["id"] for e in elements if e.get("typeName") != "genre" and e.get("id") is not None]}

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        data = net.json(f"{self.site()}/api/catalog/search?offset={self.FEED_COUNT * (max(1, page) - 1)}&sortType="
                        f"{self.FEED_ORDER.get(how, 'DATE_UPDATE')}{self._never()}", what=self.label,
                        headers={"Accept": "application/json, text/plain, */*", "Referer": self.site() + "/"})
        return self.catalog_hits(data)

    def catalog_hits(self, data) -> tuple:
        """([:class:`Hit`], whether there is a page more) of an answer of its catalog API — a title a gate closes
        (its names, its ``elements`` — :meth:`said`) not among them; the kind — its ``category``."""
        out = []
        for x in (data.get("list") if isinstance(data, dict) else None) or []:
            element = x.get("elementId") if isinstance(x, dict) and isinstance(x.get("elementId"), dict) else {}
            link = str(element.get("linkName") or "").strip("/")
            if not link or not re.fullmatch(r"[\w.-]{1,200}", link):
                continue
            names = [str(x.get("name") or link).strip()]
            if G.title(self.name, names=names, **self.said(x)):
                continue                             # a gate closed it
            category = x.get("category") if isinstance(x.get("category"), dict) else {}
            rating = x.get("rating") if isinstance(x.get("rating"), dict) else {}
            out.append(S.Hit(site=self.name, key=link, names=names, type=str(category.get("name") or ""),
                           url=f"{self.site()}/{link}", cover=str(x.get("picUrl") or ""),
                           rating=round(S._float(rating.get("rate")), 1), votes=S._int(rating.get("voteCount")),
                           chapters=S._int(x.get("chaptersCount")), adult=True))
        total = S._int(data.get("total")) if isinstance(data, dict) else 0
        offset = S._int(data.get("offset")) if isinstance(data, dict) else 0
        limit = S._int(data.get("limit")) if isinstance(data, dict) else 0
        return out, (offset + (limit or self.FEED_COUNT) < total) if total else len(out) >= self.FEED_COUNT

    def search(self, net: S.Net, query: str) -> list:
        hits = [h for h in super().search(net, query) if not G.title(self.name, names=h.names)]
        for h in hits:
            h.adult = True
        return hits
