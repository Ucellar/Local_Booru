"""(v5.44.168) Desu — of the sites of ``core/manhwa_sources.py``: its API ``/api/manga/…`` and the page of its search.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before (``manhwa_sources.Desu``).
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import quote, urljoin, urlparse

from core import manhwa_sources as S


class Desu(S.Source):
    """(v5.44.77) desu.uno (Desu.Me; desu.me itself no longer opens) — its new API, looked at from the owner's computer
    (5.11.2026; the old ``/manga/api/…`` answers 410 «api_deprecated»): ``/api/manga/<id>`` — the title (``russian``,
    ``name`` — the original one, ``synonyms``, ``kind`` — manga / manhwa / manhua…, ``status``, ``content_rating``,
    ``aired_on`` — unix time, ``description``, ``genres`` [{name, slug}], ``cover`` {preview, snippet, x120});
    ``/api/manga/<id>/chapters`` — every chapter (``id``, ``volume``, ``number``, ``title``, ``publish_date``,
    ``is_readable``, ``view_url``); ``/api/manga/<id>/chapters/<chapter id>`` — {"chapter": {…, ``pages`` [{page, url,
    width, height}]}} (as the site's reader asks: ``shared-api.js``).  Its search — the page ``/manga/search/?q=``
    (cards ``.AniMangaSearchCard``: the link ``manga/<slug>.<id>/``, the Russian and the original name, the kind, the
    year, the score).  One translation, all free; a chapter it cannot show (``is_readable`` false) is not listed."""
    name = "desu"
    feed_order = 50
    label = "Desu"
    hosts = ("desu.uno", "desu.me", "desu.city", "desu.win")
    address_value = "DESU_SITE"
    cover_hosts = ("desu.uno", "desu.me")
    KINDS = {"manga": "Манга", "manhwa": "Манхва", "manhua": "Маньхуа", "one_shot": "Манга", "doujin": "Манга",
             "comics": "Комикс", "comic": "Комикс"}
    STATUSES = {"ongoing": "Продолжается", "released": "Завершён", "anons": "Анонс", "paused": "Приостановлен",
                "discontinued": "Выпуск прекращён"}

    def site(self) -> str:
        return S.address(self.name) or S.DESU_SITE

    def headers(self, referer: str = "") -> dict:
        return {"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest",
                "Referer": referer or self.site() + "/"}

    def key_of(self, url: str) -> str:
        if not S._host_of(url, self.hosts, self.site()):
            return ""
        m = re.match(r"/manga/[^/?#]*?\.?(\d+)(?:/|$)", urlparse(url).path)
        return m.group(1) if m else ""

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        data = net.json(f"{self.site()}/api/manga/{quote(str(key), safe='')}", what=self.label, headers=self.headers())
        m = data.get("manga") if isinstance(data, dict) else None
        if not isinstance(m, dict) or not (m.get("russian") or m.get("name")):
            raise S.Missing(f"{self.label} не знает такого тайтла")
        name = str(m.get("russian") or m.get("name")).strip()
        alt = []
        for x in [m.get("name"), *(m.get("synonyms") or [])]:
            x = str(x or "").strip()
            if x and x != name and x not in alt:
                alt.append(x)
        genres = S._names(m.get("genres"))
        slugs = {str(g.get("slug") or "").casefold() for g in m.get("genres") or [] if isinstance(g, dict)}
        cover = m.get("cover") if isinstance(m.get("cover"), dict) else {}
        key = str(m.get("id") or key)
        url = str(m.get("view_url") or "").strip() or f"{self.site()}/manga/{key}/"
        return S.checked(S.TitleRef(site=self.name, key=key, url=url if url.endswith("/") else url + "/",   # (v5.44.143)
                              title=name, alt_titles=alt, type=self.KINDS.get(str(m.get("kind") or "").casefold(), ""),
                              status=self.STATUSES.get(str(m.get("status") or "").casefold(), ""),
                              year=S._year(S._date(m.get("aired_on"))),
                              age="" if str(m.get("content_rating") or "").casefold() in ("unrated", "none", "unknown")
                              else str(m.get("content_rating") or ""),
                              is_erotic=bool(slugs & {"hentai", "erotica"}) or any(
                                  g.casefold() in ("хентай", "эротика") for g in genres),
                              genres=genres, description=S.plain(m.get("description")),
                              cover_url=str(cover.get("preview") or cover.get("snippet") or cover.get("x120") or ""),
                              branches=[S.Branch(id="", name=self.label)]))

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        data = net.json(f"{self.site()}/api/manga/{quote(title.key, safe='')}/chapters", what=self.label,
                        headers=self.headers(title.url))
        found: dict = {}
        for x in (data.get("chapters") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict) or x.get("id") is None or x.get("is_readable") is False:
                continue
            ch = S.ChapterRef(id=str(x["id"]), tome=S._int(x.get("volume")), number=S._num(x.get("number")),
                            name=str(x.get("title") or "").strip(), date=S._date(x.get("publish_date")),
                            url=str(x.get("view_url") or ""))
            old = found.get((ch.tome, ch.number))           # one chapter given twice: the later one
            if old is None or S._int(ch.id) > S._int(old.id):
                found[(ch.tome, ch.number)] = ch
        out = sorted(found.values(), key=lambda c: (c.tome, S._number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        data = net.json(f"{self.site()}/api/manga/{quote(title.key, safe='')}/chapters/{quote(chapter.id, safe='')}",
                        what=self.label, headers=self.headers(chapter.url or title.url))
        found = data if isinstance(data, dict) else {}
        for wrap in ("chapter", "data"):
            if isinstance(found.get(wrap), dict) and "pages" in found[wrap]:
                found = found[wrap]
        if found.get("is_readable") is False:
            raise S.Paid(f"главу {self.label} не показывает — программа её не скачивает")
        pages = found.get("pages")
        if isinstance(pages, dict):                    # {"list": […]} — as the old API had it
            pages = pages.get("list")
        out = []
        for p in sorted((p for p in pages or [] if isinstance(p, dict) and p.get("url")), key=lambda p: S._int(p.get(
                "page"))):
            out.append(S.Picture(url=urljoin(self.site() + "/", str(p["url"])), width=S._int(p.get("width")),
                               height=S._int(p.get("height"))))
        if not out:
            raise S.SourceError(f"в главе на {self.label} нет страниц")
        return out

    _CARD = re.compile(r'<li[^>]*class="[^"]*\bAniMangaSearchCard\b[^"]*"[^>]*>(.*?)</li>', re.S)

    FEED_ORDER = {"new": "updated", "popular": "popular"}

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        """(v5.44.83) Its catalog's API ``/api/manga/catalog/?limit=20&page=&order_by=`` (as Yomi asks it)."""
        data = net.json(f"{self.site()}/api/manga/catalog/?limit=20&page={max(1, page)}&order_by="
                        f"{self.FEED_ORDER.get(how, 'updated')}", what=self.label,
                        headers={"Accept": "application/json", "Referer": self.site() + "/"})
        out = []
        for x in (data.get("mangas") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict) or not str(x.get("id") or "").isdigit():
                continue
            names = [str(n).strip() for n in (x.get("russian"), x.get("name")) if n and str(n).strip()]
            cover = x.get("cover") if isinstance(x.get("cover"), dict) else (
                x.get("image") if isinstance(x.get("image"), dict) else {})
            link = str(cover.get("preview") or cover.get("original") or "")
            out.append(S.Hit(site=self.name, key=str(x["id"]), names=list(dict.fromkeys(names)),
                           type=self.KINDS.get(str(x.get("kind") or ""), ""), url=f"{self.site()}/manga/{x['id']}",
                           cover=urljoin(self.site() + "/", link) if link else "",
                           rating=S._float(x.get("score")), adult=str(x.get("content_rating") or "") in ("hentai",)))
        pages = data.get("pagination") if isinstance(data, dict) and isinstance(data.get("pagination"), dict) else {}
        if pages:
            return out, S._int(pages.get("current_page") or page) < S._int(pages.get("last_page"))
        return out, len(out) >= 20

    def search(self, net: S.Net, query: str) -> list:
        page = net.text(f"{self.site()}/manga/search/?q={quote(query)}", what=self.label,
                        headers={"Referer": self.site() + "/"})
        out = []
        for m in self._CARD.finditer(page):
            card = m.group(1)
            href = re.search(r'<a[^>]*href="([^"]*manga/[^"/]*?\.?(\d+)/?)"', card)
            if not href:
                continue
            main = re.search(r'class="AniMangaSearchCard__title"[^>]*>(.*?)</span>', card, re.S)
            sub = re.search(r'class="AniMangaSearchCard__subtitle"[^>]*>(.*?)</span>', card, re.S)
            facts = S._block(card, "AniMangaSearchCard__facts")
            spans = [S._text(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", facts, re.S)]
            score = re.search(r'class="is-score"[^>]*>(.*?)</span>', facts, re.S)
            cover = re.search(r'<img[^>]*src="([^"]+)"', card)
            out.append(S.Hit(site=self.name, key=href.group(2),
                           names=[S._text(x.group(1)) for x in (main, sub) if x and S._text(x.group(1))],
                           type=next((x for x in spans if x in S.KNOWN_TYPES), ""),
                           year=next((int(x) for x in spans if re.fullmatch(r"(19|20)\d\d", x)), 0),
                           url=urljoin(self.site() + "/", _html.unescape(href.group(1))),
                           cover=urljoin(self.site() + "/", _html.unescape(cover.group(1))) if cover else "",
                           rating=S._float(S._text(score.group(1))) if score else 0.0))
        return out

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"
