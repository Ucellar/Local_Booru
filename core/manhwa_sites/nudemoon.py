"""(v5.44.168) Nude-Moon — of the sites of ``core/manhwa_sources.py``: a site of hentai alone behind Cloudflare's check
for browsers: its pages and pictures are the program window's browser's own requests.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before
(``manhwa_sources.NudeMoon``).
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlparse

from core import gates as G, manhwa_sources as S


# ── (v5.44.131) Nude-Moon ─────────────────────────────────────────────────────────────────────────────────────────
_NM_TAG = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["'][^"']*?/tag/([^"'/?#]+)["'][^>]*>(.*?)</a>""", re.S)
# (v5.44.132) a chapter's number in Nude-Moon's name: «… №11», «… №9-11» (an entry of several chapters at once)
_NM_NUMBER = re.compile(r"№\s*(\d+(?:[.,]\d+)?)(?:\s*[-–—]\s*(\d+(?:[.,]\d+)?))?")
_NM_SIGN_IN = re.compile(r"(?i)только\s+(?:для\s+)?(?:зарегистрированн|авторизованн)|авторизуйтесь|войдите на сайт")


def _nm_decoded(body: bytes, ctype: str = "") -> str:
    """(v5.44.131) Nude-Moon's page as text: the charset its header or its ``<meta>`` names, else UTF-8 when the bytes
    are that, else windows-1251 — its pages are windows-1251 and say so nowhere inside (its copies of 2025)."""
    if re.search(r"charset=", str(ctype or ""), re.I) or re.search(rb"<meta[^>]+charset=", bytes(body[:4096]), re.I):
        return S._decoded(body, ctype)
    try:
        return bytes(body).decode("utf-8")
    except UnicodeDecodeError:
        return bytes(body).decode("cp1251", "replace")


class NudeMoon(S.Source):
    """(v5.44.131) Nude-Moon (nude-moon.org; nude-moon.net, nude-moon.fun — its other addresses) — the owner
    (8.11.2026): «nude-moon.org надо добавить в поддержку».  Its pages answer a plain request with Cloudflare's check
    for browsers (looked at from the owner's computer): the window's browser passes it as for com-x.life
    (``Net._past_check``) — the owner: «Да, как Com-X и куки».  (v5.44.132) Its Cloudflare lets in nothing but the
    browser itself — the program's own requests refused even with the browser's pass and agent (measured 8.11.2026):
    its pages and pictures come by the browser's own requests (``Net._browser_fetch``, ``Net._browser_bytes``); the
    owner's cookies with his agent (:func:`signed_agent`) would not help.  Not in «Найти по названию»
    (``manhwa_search.ASKED``: that search never opens the window's browser) — its search is in «Лента».  A work's
    entries of several chapters at once («№9-11») are not chapters while the chapters are there one by one.

    Its pages (windows-1251; read from its copies of 2025 in the Wayback Machine and as the open readers Tachiyomi and
    Kotatsu read them): an entry ``/<number>--<slug>.html`` — its name in ``<h1>`` («Оригинал №N / Русское»,
    «Оригинал (English)»), its tags (``span.tag-links``: links ``/tag/<слово>``), «Автор» (``mangaka/``), «Перевод»
    (``/perevod/`` — the translators, its branch), «Серия» (``/seria/`` — the work it parodies), ``div.description``,
    its cover (``/images/thumb/…``); a work of chapters — each chapter an entry of its own («… №11 / …»), «Все главы»
    (``/vse_glavy/<имя>``) lists them as its lists list entries: the title is the first chapter, its chapters — the
    entries there of its name; the reader ``/<number>-online--<slug>.html`` — ``images[n].src = '/manga/…/1.jpg'`` of
    its script (its strip ``?row`` — ``<img data-src>``).  Its lists — rows ``<tr valign="top">`` (the entry's link,
    ``<h2>``, its tags as links or as words, 30 to a page, «Следующая»): «Новые» ``/all_manga?date&rowstart=``,
    «Популярное» ``/all_manga?views&rowstart=``; its search — ``/all_manga?stext=`` (windows-1251).  The cookie the open
    readers set for every visitor («NMfYa=1») is set; its mobile pages («nm_mobile») are not asked.  Everything there is
    hentai (``adult_only``); a title a gate closes (its names, its tags) is never opened or taken
    (:class:`Excluded`), nor a work one chapter of which is closed."""
    name = "nudemoon"
    feed_order, catalog_order = 100, 70
    catalog_kinds = ("manga",)
    catalog_filters = True
    label = "Nude-Moon"
    hosts = ("nude-moon.org", "nude-moon.net", "nude-moon.fun")
    address_value = "NUDEMOON_SITE"
    cover_hosts = ("nude-moon.org", "nude-moon.net", "nude-moon.fun")
    by_name = False                   # (v5.44.132) it lets in the browser alone: its search is in «Лента»
    adult_only = True
    PER_PAGE = 30                  # the entries of a page of its lists
    PARTS_PAGES = 20               # the pages of a work's «Все главы» asked at most
    FEED = {"new": "/all_manga?date&rowstart={offset}", "popular": "/all_manga?views&rowstart={offset}"}
    KEY = re.compile(r"^/+(\d+)(?:-online)?--([^/?#]+?)\.html?$")
    ENTRY = re.compile(r"""<a\b([^>]*?\bhref\s*=\s*["']((?:https?://[^"'/]+)?/*(\d+)--([^"'/?#]+?)\.html?)"""
                       r"""(?:[?#][^"']*)?["'][^>]*)>""")
    ROW = re.compile(r"""<tr\b[^>]*\bvalign\s*=\s*["']?top\b[^>]*>""", re.I)

    def site(self) -> str:
        return S.address(self.name) or S.NUDEMOON_SITE

    def key_of(self, url: str) -> str:
        if not S._host_of(url, self.hosts, self.site()):
            return ""
        m = self.KEY.match(urlparse(url).path)
        return f"{m.group(1)}--{m.group(2)}" if m else ""

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"

    def entry_url(self, key: str) -> str:
        return f"{self.site()}/{key}.html"

    def identity_key(self, key: str) -> str:
        number = str(key or "").partition("--")[0]
        return str(int(number)) if number.isdigit() else str(key or "")

    def reader_url(self, key: str) -> str:
        number, _sep, slug = str(key).partition("--")
        return f"{self.site()}/{number}-online--{slug}.html"

    @staticmethod
    def _ready(net: S.Net, url: str) -> None:
        """The cookie the open readers set for every visitor («NMfYa=1»); not its mobile pages: an «nm_mobile» the
        owner's browser had makes them otherwise."""
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return
        jar = net.session(host).cookies
        for c in [c for c in jar if c.name == "nm_mobile"]:
            jar.clear(c.domain, c.path, c.name)
        if not any(c.name == "NMfYa" for c in jar):
            jar.set("NMfYa", "1", domain=host, path="/")

    def _page(self, net: S.Net, url: str, referer: str = "") -> str:
        self._ready(net, url)
        body, ctype = net.fetch(url, what=self.label, headers={"Referer": referer or self.site() + "/",
                                                               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        return _nm_decoded(body, ctype)

    @staticmethod
    def _names(full: str) -> tuple:
        """(its own name, its other names) of an entry: «Оригинал №N / Русское» → («Оригинал №N», [«Русское»]); a name
        in brackets at the end of the first one is another name when it is of two words or more («… (Life Journal of
        a Mangaka)»), else the name's own («… Succubus (heart)» and «… (Cry)» are two works)."""
        text = " ".join(_html.unescape(str(full or "")).split())
        first, _sep, rest = text.partition(" / ")
        others = [x.strip() for x in rest.split(" / ") if x.strip()]
        main = first.strip()
        bracket = re.search(r"\s\(([^()]+)\)$", main)
        if bracket and " " in bracket.group(1).strip() and main[:bracket.start()].strip():
            others.insert(0, bracket.group(1).strip())
            main = main[:bracket.start()].strip()
        if not main and others:
            main = others.pop(0)
        return main, others

    @staticmethod
    def _tags(fragment: str) -> tuple:
        """(the tags as the site writes them, the words of their links) of a piece of a page: its links ``/tag/…``,
        else the words of a row of its lists («тентакли   комикс   …» — a tag's page writes them so)."""
        words, keys = [], []
        for key, inner in _NM_TAG.findall(fragment or ""):
            word = S._text(inner)
            if word and word not in words:
                words.append(word)
            keys.append(unquote(_html.unescape(key)).replace("_", " ").rstrip("+").strip())
        if not words:
            for inner in re.findall(r"""<span\b[^>]*class\s*=\s*["']small["'][^>]*>\s*<center>(.*?)</center>""",
                                    fragment or "", re.S):
                for word in re.split(r"\s{2,}", _html.unescape(re.sub(r"<[^>]+>", "", inner))):
                    word = word.strip()
                    if word and word not in words:
                        words.append(word)
        return words, [k for k in keys if k]

    @staticmethod
    def _kind(tags) -> str:
        """Its tag «манхва» — a manhwa (the strip of the tab «Манхва»); else a manga."""
        return "Манхва" if "манхва" in {str(t).strip().casefold() for t in tags or ()} else "Манга"

    @staticmethod
    def _links(fragment: str, part: str) -> list:
        """The words of the links whose address has ``part`` («mangaka/», «/perevod/», «/seria/»)."""
        out = []
        pattern = r"""<a\b[^>]*\bhref\s*=\s*["'][^"']*""" + re.escape(part) + r"""[^"']*["'][^>]*>(.*?)</a>"""
        for inner in re.findall(pattern, fragment or "", re.S):
            word = S._text(inner)
            if word and word not in out and word != "-":
                out.append(word)
        return out

    def _entry(self, page: str) -> dict:
        """What an entry's page (or its reader) says: its names, authors, translators, the work it parodies, its tags,
        description, cover, the address of its «Все главы»; :class:`Excluded` — a title a gate closes;
        :class:`Refused` — a page the site shows only to the signed in."""
        head = re.search(r"<h1\b[^>]*>(.*?)</h1>", page, re.S)
        full = S._text(head.group(1)) if head else ""
        if not re.search(r"\w", full):
            if _NM_SIGN_IN.search(S._text(page[:400_000])):
                raise S.Refused(f"эту страницу {self.label} показывает только тем, кто вошёл на сайт (cookies входа — "
                              "«Настройки → Манга и манхва»)")
            raise S.SourceError(f"на странице {self.label} нет тайтла")
        main, others = self._names(full)
        rest = page[head.end():]
        ends = [m.start() for m in (re.search(r"""<table\b[^>]*class\s*=\s*["']tbl["']""", rest),
                                    re.search(r"""\bid\s*=\s*["'](?:gallery|comments)["']""", rest)) if m]
        info = rest[:min([*ends, 30000])]
        links = re.search(r"""<span\b[^>]*class\s*=\s*["'][^"']*\btag-links\b[^"']*["'][^>]*>(.*?)</span>""", info,
                          re.S)
        words, keys = self._tags(links.group(1) if links else info)
        series = self._links(info, "/seria/")
        why = G.title(self.name, names=[main, *others, *series], tags=[*words, *keys])
        if why:
            raise S.Excluded(why)
        about = re.search(r"""<div\b[^>]*class\s*=\s*["']description["'][^>]*>(.*?)</div>""", info, re.S)
        cover = (re.search(r"""<img\b[^>]*?\b(?:data-src|src)\s*=\s*["']([^"']*/images/thumb/[^"']+)["']""", page)
                 or re.search(r"""<meta\b[^>]*\bproperty\s*=\s*["']og:image["'][^>]*\bcontent\s*=\s*["']([^"']+)["']""",
                              page))
        everything = re.search(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']*vse_glavy/[^"']+)["'][^>]*>\s*Все главы""", info)
        return {"main": main, "others": others, "authors": self._links(info, "mangaka/"),
                "team": ", ".join(self._links(info, "/perevod/")),
                "tags": [*series, *(w for w in words if w not in series)],
                "description": S.plain(about.group(1)) if about else "",
                "cover": urljoin(self.site() + "/", _html.unescape(cover.group(1).strip())) if cover else "",
                "all": _html.unescape(everything.group(1)) if everything else ""}

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        entry = self._entry(self._page(net, self.entry_url(key)))
        parts = self._parts(net, key, entry) if entry["all"] else []
        if parts and parts[0]["key"] != key:            # a work of chapters: the title is its first
            key = parts[0]["key"]
            entry = self._entry(self._page(net, self.entry_url(key)))
        net.kept[(self.name, key)] = parts
        if parts:                                        # its name without «№ N»; the other one if every chapter has it
            base = S._without_part(entry["main"]) or entry["main"]
            common = [x for x in entry["others"] if all(x in p["others"] for p in parts)]
            name, alt = S._shown_name(base, common)
        else:
            name, alt = S._shown_name(entry["main"], entry["others"])
        return S.TitleRef(site=self.name, key=key, url=self.entry_url(key), title=name,
                        alt_titles=[x for x in dict.fromkeys(alt) if x and x != name], type=self._kind(entry["tags"]),
                        age="18+", is_erotic=True, genres=["Хентай"], tags=entry["tags"],
                        description=entry["description"], cover_url=entry["cover"], authors=entry["authors"],
                        branches=[S.Branch(id="", name=entry["team"] or self.label)])

    def _parts(self, net: S.Net, key: str, entry: dict) -> list:
        """The chapters of the work the entry ``key`` is one of — its «Все главы»: [{key, main, others, number, range,
        name}] in their order; [] — the entry is alone.  An entry there of another name is another work; one a
        gate closes — the whole work is closed (:class:`Excluded`); (v5.44.132) one of several chapters at once
        («№9-11») — not a chapter while they are there one by one."""
        wanted = S.names_of(*(S._without_part(x) or x for x in (entry["main"], *entry["others"])))
        if not wanted:
            return []
        found: dict = {}
        url = urljoin(self.site() + "/", entry["all"])
        asked: set = set()
        for _n in range(self.PARTS_PAGES):
            if url in asked:
                break
            asked.add(url)
            try:
                page = self._page(net, url, referer=self.entry_url(key))
            except S.Missing:
                break
            for item in self._row_items(page):
                if item["key"] in found or not S.names_of(
                        *(S._without_part(x) or x for x in (item["main"], *item["others"]))) & wanted:
                    continue
                if item["forbidden"]:
                    raise S.Excluded(item["forbidden"])
                found[item["key"]] = self._part(item["key"], item["main"], item["others"])
            more = self._next(page)
            if not more:
                break
            url = urljoin(url, more)
        if key not in found:                              # beyond the pages asked: itself all the same
            found[key] = self._part(key, entry["main"], entry["others"])
        # (v5.44.132) its entries of several chapters at once («№9-11», «№0-2» of «Bound Together», 8.11.2026) — not
        # chapters of their own while the chapters are there one by one
        if any(not p["range"] and p["number"] for p in found.values()):
            found = {k: p for k, p in found.items() if not p["range"]}
        if len(found) < 2:
            return []
        parts = list(found.values())
        numbers = [p["number"] for p in parts]
        if all(numbers) and len(set(numbers)) == len(numbers):
            parts.sort(key=lambda p: S._number_key(p["number"]))
        else:                                             # in the order the site added them (their numbers)
            parts.sort(key=lambda p: S._int(p["key"].split("--", 1)[0]))
            for n, part in enumerate(parts, 1):
                part["number"] = str(n)
        return parts

    @staticmethod
    def _part(key: str, main: str, others: list) -> dict:
        """A chapter of a work: its key, names, number («№ N»; «№ A-B» — ``range``: an entry of several chapters)."""
        numbers = _NM_NUMBER.findall(main)
        if numbers:
            first, last = numbers[-1]
            return {"key": key, "main": main, "others": others, "number": S._num(first), "range": bool(last),
                    "name": ""}
        return {"key": key, "main": main, "others": others, "number": S._part_number(main), "range": False,
                "name": S._part_name(main)}

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        parts = net.kept.get((self.name, title.key))
        if parts is None:
            try:
                entry = self._entry(self._page(net, title.url or self.entry_url(title.key)))
            except S.Excluded:
                raise
            except S.SourceError:
                entry = None
            parts = self._parts(net, title.key, entry) if entry and entry["all"] else []
        parts = parts or [{"key": title.key, "number": "1", "name": ""}]
        return [S.ChapterRef(id=p["key"], number=p["number"], name=p.get("name", ""), url=self.reader_url(p["key"]),
                           index=n) for n, p in enumerate(parts, 1)]

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        url = chapter.url or self.reader_url(chapter.id)
        page = self._page(net, url, referer=self.entry_url(chapter.id))
        try:
            self._entry(page)                           # the chapter's own name and tags: the gate again
        except S.Excluded:
            raise
        except S.SourceError:
            pass
        links = re.findall(r"""\bimages\s*\[\s*\d+\s*\]\s*\.src\s*=\s*["']([^"']+)["']""", page)
        if not links:                                   # its strip: the pictures come as the page is scrolled
            strip = url.split("?", 1)[0] + "?row"
            links = [x for x in re.findall(r"""<img\b[^>]*?\bdata-src\s*=\s*["']([^"']+)["']""",
                                           self._page(net, strip, referer=url))
                     if "/manga/" in x and "/thumb" not in x]
        out = [S.Picture(url=urljoin(url, _html.unescape(x.strip()))) for x in dict.fromkeys(links) if x.strip()]
        if not out:
            # (v5.44.132) «login_err» instead of the pictures — a chapter for the signed in alone (a third of its
            # readers, 8.11.2026): the owner's sign-in from his browser goes to the window's browser too
            if "login_err" in page or _NM_SIGN_IN.search(S._text(page[:400_000])):
                raise S.Refused(f"эту главу {self.label} показывает только тем, кто вошёл на сайт: войдите на "
                              f"{urlparse(self.site()).hostname} в своём браузере — расширение программы передаст "
                              "вход само (или cookies из файла: «Настройки → Манга и манхва»)")
            raise S.SourceError(f"на странице чтения {self.label} нет картинок")
        return out

    def _row_items(self, page: str) -> list:
        """The entries of a page of its lists (and of «Все главы»): [{key, url, main, others, tags, cover, forbidden}] —
        ``forbidden``: the words of the gate that closes it (its names, its tags), "" — none does."""
        starts = list(self.ROW.finditer(page))
        out, seen = [], set()
        for n, m in enumerate(starts):
            row = page[m.start():starts[n + 1].start() if n + 1 < len(starts) else m.end() + 20000]
            cut = re.search(r"<table\b[^>]*\bstyle\s*=", row)     # the next entry's table, the column of every tag
            row = row[:cut.start()] if cut else row
            link = self.ENTRY.search(row)
            if not link:
                continue
            key = f"{link.group(3)}--{link.group(4)}"
            if key in seen:
                continue
            heading = re.search(r"<h2\b[^>]*>(.*?)</h2>", row, re.S)
            said = re.search(r"""\btitle\s*=\s*["']([^"']*)["']""", link.group(1))
            main, others = self._names(S._text(heading.group(1)) if heading else said.group(1) if said else "")
            if not re.search(r"\w", main):
                continue
            seen.add(key)
            words, keys = self._tags(row)
            picture = (re.search(r"""<img\b[^>]*?\bdata-src\s*=\s*["']([^"']+)["']""", row)
                       or re.search(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']*thumb[^"']*)["']""", row))
            cover = urljoin(self.site() + "/", _html.unescape(picture.group(1).strip())) if picture else ""
            out.append({"key": key, "url": urljoin(self.site() + "/", _html.unescape(link.group(2))), "main": main,
                        "others": others, "tags": words, "cover": cover if urlparse(cover).path.strip("/") else "",
                        "forbidden": G.title(self.name, names=[main, *others], tags=[*words, *keys])})
        return out

    @staticmethod
    def _next(page: str) -> str:
        m = re.search(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>\s*Следующая\s*</a>""", page)
        return _html.unescape(m.group(1)) if m else ""

    def _rows(self, page: str, need=(), unwanted=()) -> tuple:
        """The titles of a page of its lists: ([Hit], how many entries the page had).  (v5.44.135) ``need`` — its tags'
        words a title has every one of, ``unwanted`` — none of (the tags its row carries)."""
        items = self._row_items(page)
        wanted = {S.name_key(str(w).replace("_", " ")) for w in need if str(w).strip()}
        left = {S.name_key(str(w).replace("_", " ")) for w in unwanted if str(w).strip()}
        out = []
        for item in items:
            if item["forbidden"]:
                continue
            if wanted or left:
                has = {S.name_key(w) for w in item["tags"]}
                if wanted - has or left & has:
                    continue
            name, alt = S._shown_name(item["main"], item["others"])
            out.append(S.Hit(site=self.name, key=item["key"], names=[name, *alt], type=self._kind(item["tags"]),
                           url=item["url"], cover=item["cover"], adult=True))
        return out, len(items)

    def search_url(self, query: str) -> str:
        return f"{self.site()}/all_manga?stext={quote(str(query or '').strip().encode('cp1251', 'ignore'))}&rowstart=0"

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

    @staticmethod
    def _about(query: str, hit: S.Hit) -> bool:
        """A title its search gave is of the words asked — one of them (three letters or more) in its names or its
        address: a page of its newest titles (a search it did not take) is not an answer."""
        words = [k for k in (S.name_key(w) for w in re.findall(r"[^\W_]+", str(query or ""))) if len(k) >= 3]
        if not words:
            return True
        names = S.name_key(" ".join(hit.names)) + "|" + S.name_key(hit.key.split("--", 1)[-1])
        return any(w in names for w in words)

    def listing(self, net: S.Net, url: str, need=(), unwanted=()) -> tuple:
        """([Hit], whether there is a page more) of a page of its lists (:meth:`feed_url`, :meth:`tag_url`) or of its
        search (:meth:`search_url`: one page, its titles of the words asked alone) — the «Лента» of the manga asks it
        too (``core/catalog.py``); (v5.44.135) ``need``/``unwanted`` — its tags a title has all of / none of."""
        text = self._page(net, url)
        hits, rows = self._rows(text, need, unwanted)
        asked = dict(parse_qsl(urlparse(url).query, keep_blank_values=True, encoding="cp1251")).get("stext")
        if asked is not None:
            return [h for h in hits if self._about(asked, h)], False
        return hits, bool(self._next(text)) or rows >= self.PER_PAGE

    def search(self, net: S.Net, query: str) -> list:
        if not str(query or "").strip().encode("cp1251", "ignore").strip():
            return []                                    # nothing of it in the site's letters
        return self.listing(net, self.search_url(query))[0]

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        return self.listing(net, self.feed_url(how, page))
