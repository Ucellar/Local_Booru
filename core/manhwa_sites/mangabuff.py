"""(v5.44.168) MangaBuff — of the sites of ``core/manhwa_sources.py``: its pages: the title, its chapters (the rest of
them by ``POST /chapters/load``), a chapter's pictures.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before
(``manhwa_sources.MangaBuff``).
"""
from __future__ import annotations

import html as _html
import json
import re
from urllib.parse import quote, urljoin, urlparse

from core import manhwa_sources as S


class MangaBuff(S.Source):
    name = "mangabuff"
    feed_order, legacy_search_order = 30, 40
    label = "MangaBuff"
    hosts = ("mangabuff.ru",)
    address_value = "MANGABUFF_SITE"
    cover_hosts = ("mangabuff.ru",)

    def site(self) -> str:
        return S.address(self.name) or S.MANGABUFF_SITE

    def key_of(self, url: str) -> str:
        if not S._ours(url, self.site(), self.hosts):
            return ""
        m = re.match(r"/manga/([^/?#]+)", urlparse(url).path)
        return m.group(1) if m else ""

    @staticmethod
    def _text(fragment: str) -> str:
        return S._text(fragment)

    def _block(self, page: str, cls: str) -> str:
        """The inside of the first element with the class ``cls`` (to its closing tag at the same depth)."""
        return S._block(page, cls)

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        url = f"{self.site()}/manga/{key}"
        page = net.text(url, what="MangaBuff")
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        if not h1:
            raise S.SourceError("на странице MangaBuff нет тайтла")
        name = self._text(h1.group(1))
        alt_block = self._block(page, "manga__name-alt")
        alt = [self._text(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", alt_block, re.S)] or \
            [x.strip() for x in self._text(alt_block).split(" / ") if x.strip()]
        middle = self._block(page, "manga__middle-links")
        links = [self._text(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", middle, re.S)]
        kind = next((x for x in links if x in S.KNOWN_TYPES), "")
        year = next((int(x) for x in links if re.fullmatch(r"(19|20)\d\d", x)), 0)
        status = next((x for x in links if x in S.KNOWN_STATUSES), "")
        tags_block = self._block(page, "tags")
        genres = [self._text(x) for x in re.findall(r"<a[^>]*>(.*?)</a>", tags_block, re.S)]
        genres = [g for g in genres if g and len(g) < 40]
        about = S.plain(self._block(page, "manga__description"))
        poster = re.search(r'<meta property="og:image" content="([^"]+)"', page)
        site_id = re.search(r'class="manga[\s"][^>]*data-id="(\d+)"', page) or re.search(r'data-manga-id="(\d+)"', page)
        erotic = any(g.lower() in ("хентай", "эротика", "hentai") for g in genres) or \
            bool(re.search(r'class="[^"]*\bmanga__age[^"]*"[^>]*>\s*18\+', page))
        return S.checked(S.TitleRef(site=self.name, key=key, url=url, title=name,       # (v5.44.143) the program's gate
                              alt_titles=[a for a in alt if a and a != name], type=kind, status=status, year=year,
                              is_erotic=erotic, genres=genres, description=about,
                              cover_url=_html.unescape(poster.group(1)) if poster else "",
                              branches=[S.Branch(id="", name="MangaBuff")], site_id=site_id.group(1) if site_id else ""))

    _ITEM = re.compile(r'<a[^>]+href="([^"]+/manga/[^"/]+/(\d+)/([\d.]+))"[^>]*class="chapters__item[^"]*"[^>]*>(.*?)</a>'
                       r'|<a[^>]+class="chapters__item[^"]*"[^>]*href="([^"]+/manga/[^"/]+/(\d+)/([\d.]+))"[^>]*>(.*?)</a>',
                       re.S)

    def _items(self, page: str) -> list:
        out = []
        for m in self._ITEM.finditer(page):
            href, tome, number, inner = (m.group(1), m.group(2), m.group(3), m.group(4)) if m.group(1) else \
                (m.group(5), m.group(6), m.group(7), m.group(8))
            tag = m.group(0)[:m.group(0).find(">") + 1]
            date = re.search(r'data-chapter-date="([^"]*)"', tag)
            own = re.search(r'class="chapters__name"[^>]*>(.*?)</div>', inner or "", re.S)
            out.append(S.ChapterRef(id=f"{tome}/{number}", tome=S._int(tome), number=S._num(number),
                                  name=self._text(own.group(1)) if own else "", date=date.group(1) if date else "",
                                  url=urljoin(self.site() + "/", _html.unescape(href))))
        return out

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        page = net.text(title.url, what="MangaBuff")
        found = {ch.id: ch for ch in self._items(page)}
        if "load-chapters-trigger" in page or "chapters/load" in page or len(found) >= 100:
            token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
            manga_id = title.site_id or (re.search(r'data-manga-id="(\d+)"', page) or [None, ""])[1]
            if token and manga_id:
                body, _ctype = net.fetch(f"{self.site()}/chapters/load", method="POST", data={"manga_id": manga_id},
                                         headers={"X-CSRF-TOKEN": token.group(1), "X-Requested-With": "XMLHttpRequest",
                                                  "Accept": "application/json", "Referer": title.url},
                                         what="MangaBuff")
                try:
                    more = json.loads(body.decode("utf-8", "replace")).get("content") or ""
                except (ValueError, AttributeError):
                    more = ""
                for ch in self._items(str(more)):
                    found.setdefault(ch.id, ch)
        out = sorted(found.values(), key=lambda ch: (ch.tome, S._number_key(ch.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        page = net.text(chapter.url, what="MangaBuff", headers={"Referer": title.url})
        out = []
        for item in re.findall(r'<div[^>]*class="reader__item[^"]*"[^>]*>(.*?)</div>', page, re.S):
            img = re.search(r'<img[^>]*?(?:data-src|src)="([^"]+)"', item)
            if img and not img.group(1).startswith("data:"):
                out.append(S.Picture(url=urljoin(chapter.url, _html.unescape(img.group(1)))))
        if not out:
            if re.search(r"(?i)купить|платн|доступ[а-я]* по подписке", self._text(self._block(page, "reader"))):
                raise S.Paid("глава закрыта на MangaBuff — программа такие не скачивает")
            raise S.SourceError("на странице главы MangaBuff нет картинок")
        return out

    _CARD = re.compile(r'<a\b([^>]*\bclass="[^"]*\bcards__item\b[^"]*"[^>]*)>(.*?)</a>', re.S)

    def search(self, net: S.Net, query: str) -> list:
        page = net.text(f"{self.site()}/search?type=manga&q={quote(query)}", what="MangaBuff")
        return self._cards(page)

    FEED_ORDER = {"new": "updated_at", "popular": "real_views"}

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        """Its catalog page ``/manga?sort=…&page=N`` — the cards as its search has them."""
        text = net.text(f"{self.site()}/manga?sort={self.FEED_ORDER.get(how, 'updated_at')}"
                        + (f"&page={page}" if page > 1 else ""), what="MangaBuff")
        hits = self._cards(text)
        more = bool(re.search(r'class="pagination__button[^"]*"[^>]*>\s*<a[^>]*>\s*Вперёд', text)) or \
            bool(re.search(rf'[?&]page={page + 1}\b', text))
        return hits, more

    def _cards(self, page: str) -> list:
        out = []
        for m in self._CARD.finditer(page):
            href = re.search(r'href="([^"]+)"', m.group(1))
            slug = re.search(r"/manga/([^/?#]+)", _html.unescape(href.group(1))) if href else None
            if not slug:
                continue
            name = re.search(r'class="cards__name"[^>]*>(.*?)</div>', m.group(2), re.S)
            info = re.search(r'class="cards__info"[^>]*>(.*?)</div>', m.group(2), re.S)
            kind = self._text(info.group(1)).split(",")[0].strip() if info else ""
            # (v5.44.75) the poster in the card's style: «background-image: url('/x180/img/manga/posters/….jpg?…')»
            poster = re.search(r"background-image:\s*url\(\s*['\"]?([^'\")]+)", _html.unescape(m.group(2)))
            rating = re.search(r'class="cards__rating[^"]*"[^>]*>\s*([\d.,]+)\s*<', m.group(2))
            out.append(S.Hit(site=self.name, key=slug.group(1), names=[self._text(name.group(1))] if name else [],
                           type=kind if kind in S.KNOWN_TYPES else "", url=f"{self.site()}/manga/{slug.group(1)}",
                           cover=urljoin(self.site() + "/", poster.group(1).strip()) if poster else "",
                           rating=S._float(rating.group(1)) if rating else 0.0))
        return out

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"
