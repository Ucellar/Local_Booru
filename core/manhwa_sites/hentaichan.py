"""(v5.44.168) Хентай-тян — of the sites of ``core/manhwa_sources.py``: a site of hentai alone; a work of several parts
is one title there, its parts — the chapters.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before
(``manhwa_sources.Hentaichan``).
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import quote, unquote, urljoin, urlparse

from core import gates as G, manhwa_sources as S


CLOUD = 60                   # a column of more tags than this is the site's every tag, not a title's


def _chan_names(full) -> tuple:
    """Хентай-тян's name of an entry, «Главное (Другое)» → ("Главное", ["Другое", …]): the one in brackets at its end
    is another name (Russian, Japanese, English — «A / B», «A | B» are two); without them — itself alone."""
    text = " ".join(_html.unescape(str(full or "")).split())
    if text.endswith(")"):
        depth = 0
        for i in range(len(text) - 1, -1, -1):
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
                if depth == 0:
                    inner, outer = text[i + 1:-1].strip(), text[:i].strip()
                    if inner and outer:
                        return outer, [x.strip() for x in re.split(r"\s+[/|]\s+", inner) if x.strip()]
                    break
    return text, []


class Hentaichan(S.Source):
    """(v5.44.85) Хентай-тян (hentaichan.live; its pages move from mirror to mirror — x5, x9, x17.h-chan.me…) — the
    owner (5.11.2026): «https://hentaichan.live/ еще добавь в поддерживаемые».  Pages of DataLife Engine, as they are
    made (its copies of 2025–2026 in the Wayback Machine, read from the owner's computer: the site itself did not answer
    then) and as the readers Tachiyomi (HenChan) and Kotatsu read them: an entry ``/manga/<id>-<slug>.html`` (the site
    takes it by the number: «/related/10704-undefined.html» answers) — its name in ``a.title_top_a`` («Главное
    (Другое)»: English or Japanese and Russian), the rows of ``#info_wrap`` (``.item`` — ``.item2``: «Аниме/манга» —
    the work it parodies, «Автор», «Переводчик»), ``#description``, ``img#cover``, its tags — the column ``.sidetags``
    (``li.sidetag``: «+», «-», the tag; a list page's column is the site's every tag); the parts of one work are entries
    of their own (« - глава 5», « - часть 2. Имя»), ``/related/<id>-<slug>.html`` lists them all («Все главы»,
    ``div.related`` → ``.related_info h2 a``; ten to a page, ``#pagination_related`` «Вперед» — ``?offset=``) — of an
    entry alone it lists others («Хентай похожий на …»): one title — the first part, its parts — its chapters; a part's
    pictures — ``/online/<id>-<slug>.html``: ``"fullimg": ['…', …]`` of its script (``mimg4.imgschan.xyz``).  Its lists —
    ``div.content_row`` (``h2 a``, its picture, its tags — the links of ``.genre``; entries taken down are named «.»):
    «Обновления» ``/manga/newest?offset=`` (20 to a page), «Популярное» ``/mostviews&sort=manga?offset=``; its search —
    DataLife Engine's ``/?do=search&subaction=search&story=``.  Everything there is hentai (``adult_only``); a title
    a gate closes is never opened or taken (:class:`Excluded`)."""
    name = "hentaichan"
    feed_order, catalog_order = 90, 40
    catalog_kinds = ("manga",)
    label = "Хентай-тян"
    hosts = ("hentaichan.live", "h-chan.me", "henchan.pro", "hentaichan.ru")
    address_value = "HENTAICHAN_SITE"
    cover_hosts = ("imgschan.xyz", "hentaichan.live", "h-chan.me", "henchan.pro")
    adult_only = True
    slow_pages = True              # (v5.44.95) 12–15 s a page at times (6.11.2026): its feed waits longer (manhwa_feed)
    PER_PAGE = 20                  # the titles of a page of its lists
    PARTS_PAGES = 40               # the pages of a work's parts asked at most (ten parts to a page)
    FEED = {"new": "/manga/newest?offset={offset}", "popular": "/mostviews&sort=manga?offset={offset}"}
    KEY = re.compile(r"/(?:manga|online|related|download)/+(\d+(?:-[^/]*?)?)\.html?$")
    LINK = re.compile(r'<a\b([^>]*\bhref=["\']([^"\']*/manga/+(\d+(?:-[^"\'/]*?)?)\.html?)(?:\?[^"\']*)?["\'][^>]*)>'
                      r'(.*?)</a>', re.S)
    ROW = re.compile(r'<div\b[^>]*class=["\'][^"\']*\bcontent_row\b[^"\']*["\']([^>]*)>')

    def site(self) -> str:
        return S.address(self.name) or S.HENTAICHAN_SITE

    def key_of(self, url: str) -> str:
        if not S._host_of(url, self.hosts, self.site()):
            return ""
        m = self.KEY.match(urlparse(url).path)
        return m.group(1) if m else ""

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"

    def _page(self, net: S.Net, url: str, referer: str = "") -> str:
        body, ctype = net.fetch(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        return S._decoded(body, ctype)

    @staticmethod
    def _rows_of(page: str) -> dict:
        """The rows of ``#info_wrap``: {«Автор»: [its words], …}."""
        out: dict = {}
        for label, value in re.findall(r'class=["\']item["\'][^>]*>(.*?)</div>\s*<div[^>]*class=["\']item2["\'][^>]*>'
                                       r'(.*?)</div>', S._by_id(page, "info_wrap"), re.S):
            words = [S._text(x) for x in re.findall(r"<a\b[^>]*>(.*?)</a>", value, re.S)]
            out.setdefault(S._text(label).rstrip(":").strip(), [x for x in words if x] or [S._text(value)])
        return out

    @staticmethod
    def _tags_of(page: str) -> tuple:
        """(the entry's tags as the site writes them, the keys of their links): its column ``.sidetags`` — a column of
        more than :data:`CLOUD` is the site's every tag, not the entry's."""
        items = re.findall(r'<li[^>]*class=["\'][^"\']*\bsidetag\b[^"\']*["\'][^>]*>(.*?)</li>', S._block(page, "sidetags"),
                           re.S)
        words, keys = [], []
        if len(items) > CLOUD:
            return words, keys
        for item in items:
            links = re.findall(r"<a\b([^>]*)>(.*?)</a>", item, re.S)
            if not links:
                continue
            attrs, inner = links[-1]                  # its «+», «-» first, the tag itself last
            word = S._text(inner).replace("_", " ").strip()
            key = re.search(r'/tags/([^"\'/?#&<>]+)', attrs)
            if word and word not in ("+", "-", "−") and word not in words:
                words.append(word)
            if key:
                keys.append(unquote(_html.unescape(key.group(1))))
        return words, keys

    def _entry(self, page: str) -> dict:
        """What an entry's page says: its names, authors, translators, tags, description, cover; :class:`Missing` — no
        such entry (DataLife Engine's «публикаций не найдено»: none, or only for those signed in)."""
        top = re.search(r'<a\b[^>]*class=["\'][^"\']*\btitle_top_a\b[^"\']*["\'][^>]*>(.*?)</a>', page, re.S)
        heading = re.search(r"<title>(.*?)</title>", page, re.S)
        full = S._text(top.group(1)) if top else ""
        if not full and heading:
            full = S._text(heading.group(1)).split(" »")[0].strip()
        if not re.search(r"\w", full) or not (S._by_id(page, "info_wrap") or re.search(r'<img\b[^>]*\bid=["\']cover', page)):
            if re.search(r"(?i)публикаций на сайте не найдено|нет доступа для просмотра", page):
                raise S.Missing(f"{self.label}: такой страницы нет (или её видят только те, кто вошёл на сайт)")
            raise S.SourceError(f"на странице {self.label} нет тайтла")
        main, others = _chan_names(full)
        rows = self._rows_of(page)
        words, keys = self._tags_of(page)
        series = [x for x in rows.get("Аниме/манга", rows.get("Серия", [])) if x and x != "Оригинальные работы"]
        why = G.title(self.name, tags=[*words, *keys])
        if why:
            raise S.Excluded(why)
        about = S._by_id(page, "description")
        cut = re.search(r"<div\b", about)
        cover = re.search(r'<img\b[^>]*\bid=["\']cover["\'][^>]*>', page)
        src = re.search(r'\b(?:data-src|src)=["\']([^"\']+)["\']', cover.group(0)) if cover else None
        team = [x for x in rows.get("Переводчик", []) if x and x != "-"]
        return {"main": main, "others": others, "authors": [x for x in rows.get("Автор", []) if x and x != "-"],
                "team": ", ".join(team), "tags": [*series, *(w for w in words if w not in series)],
                "description": S.plain(about[:cut.start()] if cut else about),
                "cover": urljoin(self.site() + "/", _html.unescape(src.group(1))) if src and src.group(1).strip() else ""}

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        entry = self._entry(self._page(net, f"{self.site()}/manga/{key}.html"))
        parts = self._parts(net, key, entry["main"])
        if parts and parts[0]["key"] != key:            # a work of parts: the title is its first part
            key = parts[0]["key"]
            entry = self._entry(self._page(net, f"{self.site()}/manga/{key}.html"))
        net.kept[(self.name, key)] = parts
        if parts:                                        # its name without «глава N»; the other one if every part has it
            base = S._without_part(entry["main"]) or entry["main"]
            common = [x for x in entry["others"] if all(x in p["others"] for p in parts)]
            name, alt = S._shown_name(base, common)
        else:
            name, alt = S._shown_name(entry["main"], entry["others"])
        return S.TitleRef(site=self.name, key=key, url=f"{self.site()}/manga/{key}.html", title=name,
                        alt_titles=[x for x in dict.fromkeys(alt) if x and x != name], type="Манга", age="18+",
                        is_erotic=True, genres=["Хентай"], tags=entry["tags"], description=entry["description"],
                        cover_url=entry["cover"], authors=entry["authors"],
                        branches=[S.Branch(id="", name=entry["team"] or self.label)])

    def _parts(self, net: S.Net, key: str, main: str) -> list:
        """The parts of the work the entry ``key`` is one of — its «Все главы» (``/related/``): [{key, main, others,
        number, name}] in their order; [] — the entry is alone («похожий на» — other works; 404).  An entry there of
        another name is another work."""
        wanted = S.names_of(S._without_part(main))
        if not wanted:
            return []
        found: dict = {}
        url = f"{self.site()}/related/{key}.html"
        for _n in range(self.PARTS_PAGES):
            try:
                page = self._page(net, url, referer=f"{self.site()}/manga/{key}.html")
            except S.Missing:
                break
            heading = re.search(r"<title>(.*?)</title>", page, re.S)
            if heading and re.search(r"(?i)похож", S._text(heading.group(1))):
                return []                               # «Хентай похожий на …» — other works, not its parts
            for m in re.finditer(r'<(\w+)\b[^>]*class=["\']related_info["\'][^>]*>', page):
                link = self.LINK.search(S._inside(page, m))
                if not link or link.group(3) in found:
                    continue
                said = re.search(r'\btitle=["\']([^"\']*)["\']', link.group(1))
                other, others = _chan_names(S._text(link.group(4)) or (said.group(1) if said else ""))
                if S.names_of(S._without_part(other)) & wanted:
                    found[link.group(3)] = {"key": link.group(3), "main": other, "others": others,
                                            "number": S._part_number(other), "name": S._part_name(other)}
            more = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>\s*Вперед', S._by_id(page, "pagination_related"))
            if not more:
                break
            url = urljoin(url, _html.unescape(more.group(1)))
        if key not in found or len(found) < 2:
            return []
        parts = list(found.values())
        numbers = [p["number"] for p in parts]
        if all(numbers) and len(set(numbers)) == len(numbers):
            parts.sort(key=lambda p: S._number_key(p["number"]))
        else:                                         # in the order the site added them (their numbers)
            parts.sort(key=lambda p: S._int(p["key"].split("-", 1)[0]))
            for n, part in enumerate(parts, 1):
                part["number"] = str(n)
        return parts

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        parts = net.kept.get((self.name, title.key))
        if parts is None:
            try:
                main = S._text(re.search(r"<title>(.*?)</title>", self._page(net, title.url), re.S).group(1))
            except (S.SourceError, AttributeError):
                main = ""
            parts = self._parts(net, title.key, main.split(" »")[0]) if main else []
        parts = parts or [{"key": title.key, "number": "1", "name": ""}]
        return [S.ChapterRef(id=p["key"], number=p["number"], name=p.get("name", ""),
                           url=f"{self.site()}/online/{p['key']}.html", index=n) for n, p in enumerate(parts, 1)]

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        url = chapter.url or f"{self.site()}/online/{chapter.id}.html"
        page = self._page(net, url, referer=f"{self.site()}/manga/{chapter.id}.html")
        found = re.search(r"""["']fullimg["']\s*:\s*\[(.*?)\]""", page, re.S)
        links = re.findall(r"""["']([^"'\s]+)["']""", found.group(1)) if found else []
        out = [S.Picture(url=urljoin(url, _html.unescape(x.replace("\\/", "/")))) for x in links
               if x.strip() and "/manganew_mp4/" not in x]       # a video among the pages is not a page
        if not out:
            if re.search(r"(?i)зарегистрир|авториз|войдите на сайт", S._text(page[:400_000])):
                raise S.Refused(f"главу {self.label} показывает только тем, кто вошёл на сайт (cookies входа — "
                              "«Настройки → Манга и манхва»)")
            raise S.SourceError(f"на странице чтения {self.label} нет картинок")
        return out

    def _rows(self, page: str) -> tuple:
        """The titles of a page of its lists (``div.content_row``): ([Hit], how many rows the page had)."""
        starts = list(self.ROW.finditer(page))
        out, seen = [], set()
        for n, m in enumerate(starts):
            row = page[m.start():starts[n + 1].start() if n + 1 < len(starts) else m.end() + 20000]
            kind = re.search(r'title=["\']Выбрать только ([^"\']*)["\']', row)
            if re.search(r'class=["\']item["\'][^>]*>\s*Тип\s*<', row) or kind and re.search(r"(?i)игр|видео", kind.group(1)):
                continue                               # no manga: a game, a video
            heading = re.search(r"<h2\b[^>]*>(.*?)</h2>", row, re.S)
            link = (self.LINK.search(heading.group(1)) if heading else None) or self.LINK.search(row)
            if not link or link.group(3) in seen:
                continue
            tags = S._block(row, "genre")
            words = [S._text(x) for x in re.findall(r"<a\b[^>]*>(.*?)</a>", tags, re.S)]
            if G.title(self.name, tags=[*words, *re.findall(r'/tags/([^"\'/?#&<>]+)', tags)]):
                continue                               # a gate closed it
            main, others = _chan_names(S._text(link.group(4)))
            if not re.search(r"\w", main):
                continue                               # an entry taken down: its name is «.»
            name, alt = S._shown_name(main, others)
            picture = (re.search(r'<img\b[^>]*\bdata-src=["\']([^"\']+)["\']', row)
                       or re.search(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', row))
            cover = urljoin(self.site() + "/", _html.unescape("".join(picture.group(1).split()))) if picture else ""
            seen.add(link.group(3))
            out.append(S.Hit(site=self.name, key=link.group(3), names=[name, *alt], type="Манга",
                           url=urljoin(self.site() + "/", _html.unescape(link.group(2))),
                           cover=cover if urlparse(cover).path.strip("/") else "", adult=True))
        return out, len(starts)

    def search_url(self, query: str) -> str:
        return (f"{self.site()}/?do=search&subaction=search&search_start=1&full_search=0&result_from=1"
                f"&result_num=40&story={quote(str(query or ''))}")

    def feed_url(self, how: str, page: int) -> str:
        return self.site() + self.FEED.get(how, self.FEED["new"]).format(offset=self.PER_PAGE * (max(1, page) - 1))

    TAG_ORDER = {"popular": "views", "new": "date"}

    def tag_url(self, words, page: int, how: str = "new") -> str:
        """(v5.44.135) A page of its titles with every one of its tags ``words`` («большие_груди»): ``/tags/a+b`` — the
        site joins them (asked from the owner's computer 8.11.2026: «большие_груди+ahegao» — 20 titles of 20 with both,
        and its second page so); its orders — ``&views``, ``&date`` (its own links there); 30 a step as its lists."""
        path = "+".join(quote(str(w).strip().replace(" ", "_")) for w in words if str(w).strip())
        order = self.TAG_ORDER.get(how, "date")
        return f"{self.site()}/tags/{path}&{order}&rowstart={self.PER_PAGE * (max(1, page) - 1)}"

    def listing(self, net: S.Net, url: str) -> tuple:
        """([Hit], whether there is a page more) of a page of its lists (:meth:`feed_url`, :meth:`search_url`: one
        page) — (v5.44.93) the «Лента» of the manga asks it too (``core/catalog.py``)."""
        return self._listing(net, url)[:2]

    def _listing(self, net: S.Net, url: str) -> tuple:
        text = self._page(net, url)
        hits, rows = self._rows(text)
        if "do=search" in url:
            return hits, False, rows
        return hits, bool(re.search(r">\s*(?:Вперед|Вперёд|Далее)\s*<", text)) or rows >= self.PER_PAGE, rows

    def search(self, net: S.Net, query: str) -> list:
        return self.listing(net, self.search_url(query))[0]

    # (v5.44.93) its «Популярное» (and «Избранное») gave 19 bytes and no rows after 9–27 s, asked from the owner's
    # computer on 6.11.2026, while «Обновления» gave its 20 titles: once empty, «Обновления» instead for this run
    popular_empty = False

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        if how == "popular" and not Hentaichan.popular_empty:
            hits, more, rows = self._listing(net, self.feed_url("popular", page))
            if rows or page > 1:
                return hits, more
            Hentaichan.popular_empty = True
            S._LOG.info("%s: its «Популярное» is empty — «Обновления» instead", self.label)
        return self.listing(net, self.feed_url("new", page))
