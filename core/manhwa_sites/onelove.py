"""(v5.44.168) Manga One Love — of the sites of ``core/manhwa_sources.py``: its pages — the chapters ReadManga does not
give.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before
(``manhwa_sources.MangaOneLove``).
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin, urlparse

from core import manhwa_sources as S


class MangaOneLove(S.Source):
    """(v5.44.143) Manga One Love (mangaonelove.space) — the owner, 9.11.2026, of ReadManga, which gives the program none
    of its pictures: «ну не обходи но сделай так что бы качало».  «Король меча», Глава 185 is free here and on no other
    site the program reads (Remanga and ReComics sell it, com-x.life has no such chapter, MangaLib, MangaBuff, Desu —
    no such title): its pages and pictures come to the program openly (asked from the owner's computer 9.11.2026 —
    real JPEGs, no check for robots).  WordPress with the theme Madara and its own pages, as they were that day:

    * the title — ``/manga/<slug>/``: ``h1.manga-title``; the rows ``<span class="info-label">…:</span>`` — «Тип»,
      «Статус» («Онгоинг»), «Автор(ы)», «Переводчики», «Другое название», «Релиз» (the year); the genres and the tags
      — the links ``a.info-tag`` to ``/manga-genre/…`` and ``/manga-tag/…`` (the menus' links of genres have no such
      class); the description — ``.description-full``; the cover — ``og:image``;
    * its chapters, all of them on that page, newest first — ``a.chapter-item`` (``data-chapter-number``,
      ``data-volume-number`` — 0 without a tome; ``.chapter-number`` «185 Глава», ``.chapter-date`` «21.05.2024»),
      each to ``/manga-reader/<slug>/glava-<N>/stranica-1/``;
    * a chapter's page — every picture of it: ``img.feed-page__image`` (``data-page``, its size);
    * its search — the quick search its pages ask (``/wp-admin/admin-ajax.php``, POST ``action=wp-manga-search-manga``,
      ``title``): JSON ``{success, data: [{title, url, type}]}``.

    One translation to a chapter; all free.  The genre «Взрослая» (as «Хентай», «Эротика») — erotica."""
    name = "onelove"
    label = "Manga One Love"
    hosts = ("mangaonelove.space",)
    address_value = "ONELOVE_SITE"
    cover_hosts = ("mangaonelove.space",)
    EROTIC = ("взрослая", "хентай", "эротика", "18+")
    STATUSES = {"онгоинг": "Продолжается", "завершен": "Завершён", "завершён": "Завершён", "заморожен": "Заморожен"}

    def site(self) -> str:
        return S.address(self.name) or S.ONELOVE_SITE

    def key_of(self, url: str) -> str:
        if not S._host_of(url, self.hosts, self.site()):
            return ""
        m = re.match(r"/(?:manga|manga-reader)/([a-z0-9][a-z0-9_-]*)(?:/|$)", urlparse(url).path, re.I)
        return m.group(1) if m and m.group(1).lower() != "page" else ""

    def _page(self, net: S.Net, url: str, referer: str = "") -> str:
        return net.text(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    _VALUE = re.compile(r'\s*<(\w+)\b[^>]*\bclass="[^"]*\binfo-value\b[^"]*"[^>]*>')

    @classmethod
    def _rows(cls, page: str) -> dict:
        """The rows of facts — the element ``.info-value`` right after each label: «Тип» → its words; a row of links —
        their words, one after another (« / »)."""
        out = {}
        for m in re.finditer(r'<span class="info-label">\s*([^<]+?)\s*</span>', page):
            value = cls._VALUE.match(page, m.end())
            if not value:
                continue
            inner = S._inside(page, value)
            links = [x for x in (S._text(a) for a in re.findall(r"<a\b[^>]*>(.*?)</a>", inner, re.S)) if x]
            out.setdefault(m.group(1).rstrip(":").strip(), " / ".join(links) if links else S._text(inner))
        return out

    @staticmethod
    def _tags(page: str, kind: str) -> list:
        out = []
        for x in re.findall(r'<a href="[^"]*/manga-' + kind + r'/[^"]*"\s+class="info-tag[^"]*"[^>]*>(.*?)</a>', page,
                            re.S):
            x = S._text(x)
            if x and x not in out:
                out.append(x)
        return out

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        url = f"{self.site()}/manga/{key}/"
        page = self._page(net, url)
        h1 = re.search(r'<h1[^>]*class="[^"]*\bmanga-title\b[^"]*"[^>]*>(.*?)</h1>', page, re.S)
        name = S._text(h1.group(1)) if h1 else ""
        if not name:
            raise S.SourceError(f"на странице {self.label} нет тайтла")
        rows = self._rows(page)
        alt = [x.strip() for x in rows.get("Другое название", "").split(" / ") if x.strip() and x.strip() != name]
        genres = self._tags(page, "genre")
        kind = rows.get("Тип", "")
        cover = re.search(r'<meta property="og:image" content="([^"]+)"', page)
        net.kept[(self.name, key)] = page                # its chapters are on this page: not asked again
        return S.checked(S.TitleRef(                           # the program's gate
            site=self.name, key=key, url=url, title=name, alt_titles=list(dict.fromkeys(alt)),
            type=kind if kind in S.KNOWN_TYPES else "", year=S._year(rows.get("Релиз", "")),
            status=self.STATUSES.get(rows.get("Статус", "").casefold(), rows.get("Статус", "")),
            is_erotic=any(g.casefold() in self.EROTIC for g in genres), genres=genres, tags=self._tags(page, "tag"),
            description=S.plain(S._block(page, "description-full")),
            cover_url=_html.unescape(cover.group(1)) if cover else "",
            authors=[x.strip() for x in rows.get("Автор(ы)", "").split(" / ") if x.strip()],
            branches=[S.Branch(id="", name=rows.get("Переводчики", "") or self.label)]))

    _CHAPTER = re.compile(r'<a\b([^>]*\bclass="chapter-item"[^>]*)>(.*?)</a>', re.S)

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        page = net.kept.pop((self.name, title.key), None) or self._page(net, title.url)
        found: dict = {}
        for m in self._CHAPTER.finditer(page):
            attrs, inner = m.group(1), m.group(2)
            href = re.search(r'href="([^"]+)"', attrs)
            number = re.search(r'data-chapter-number="([^"]*)"', attrs)
            if not href or not number or not re.fullmatch(r"\d+(?:[.,]\d+)?", number.group(1).strip()):
                continue
            link = urljoin(title.url, _html.unescape(href.group(1)))
            slug = re.search(r"/manga-reader/[^/]+/([^/?#]+)", link)
            volume = re.search(r'data-volume-number="(\d*)"', attrs)
            tome = S._int(volume.group(1)) if volume else 0
            n = S._num(number.group(1))
            words = re.search(r'class="chapter-number"[^>]*>(.*?)</span>', inner, re.S)
            own = re.sub(r"^\s*[\d.,]+\s*(?:Глава|глава)?\s*[-–—:.]?\s*", "", S._text(words.group(1)) if words else "")
            day = re.search(r'class="chapter-date"[^>]*>\s*(\d\d)\.(\d\d)\.(\d{4})', inner)
            found.setdefault((tome, n), S.ChapterRef(
                id=slug.group(1) if slug else f"{tome}/{n}", tome=tome, number=n, name=own,
                date=f"{day.group(3)}-{day.group(2)}-{day.group(1)}" if day else "", url=link))
        out = sorted(found.values(), key=lambda c: (c.tome, S._number_key(c.number)))
        for k, ch in enumerate(out, 1):
            ch.index = k
        return out

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        page = self._page(net, chapter.url, referer=title.url)
        out = []
        for tag in re.findall(r'<img\b[^>]*\bclass="[^"]*\bfeed-page__image\b[^"]*"[^>]*>', page, re.S):
            src = re.search(r'\b(?:data-src|src)="\s*([^"\s]+)\s*"', tag)
            if not src or src.group(1).startswith("data:"):
                continue
            size = {k: S._int(v) for k, v in re.findall(r'\b(width|height|data-page)="(\d+)"', tag)}
            out.append((size.get("data-page") or len(out) + 1,
                        S.Picture(url=urljoin(chapter.url, _html.unescape(src.group(1))), width=size.get("width", 0),
                                height=size.get("height", 0))))
        if not out:
            raise S.SourceError(f"на странице главы {self.label} нет картинок")
        return [pic for _place, pic in sorted(out, key=lambda x: x[0])]

    def search(self, net: S.Net, query: str) -> list:
        data = net.json(f"{self.site()}/wp-admin/admin-ajax.php", method="POST", what=self.label,
                        data={"action": "wp-manga-search-manga", "title": query},
                        headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                 "X-Requested-With": "XMLHttpRequest", "Referer": self.site() + "/"})
        if not isinstance(data, dict) or not data.get("success"):
            return []                                    # «nothing found» comes as a message, not titles
        out = []
        for x in data.get("data") or []:
            if not isinstance(x, dict):
                continue
            url = str(x.get("url") or "")
            key = self.key_of(url)
            name = _html.unescape(str(x.get("title") or "")).strip()
            if key and name:
                out.append(S.Hit(site=self.name, key=key, names=[name], url=f"{self.site()}/manga/{key}/"))
        return out

    def referer(self, title: S.TitleRef) -> str:
        return title.url
