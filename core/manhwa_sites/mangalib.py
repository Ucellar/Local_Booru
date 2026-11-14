"""(v5.44.168) MangaLib and HentaiLib (one API) — of the sites of ``core/manhwa_sources.py``: the title, its teams'
chapters, a chapter's pages; the titles 18+ — with the owner's token.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before
(``manhwa_sources.MangaLib``).
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from core import gates as G, manhwa_sources as S


class MangaLib(S.Source):
    """(v5.44.61) mangalib.me — its API (``Site-Id: 1``; looked at from the owner's computer, 4.11.2026):
    ``/api/manga/<id--slug>?fields[]=…`` — the title (``rus_name``, ``eng_name``, ``otherNames``, ``type``, ``status``,
    ``ageRestriction``, ``releaseDate``, ``summary`` — a document of paragraphs, ``genres``, ``tags``, ``authors``,
    ``cover``); ``/api/manga/<id--slug>/chapters`` — every chapter with its translations (``branches``: ``branch_id``,
    its ``teams``; a paid one — ``restricted_view`` {is_open: false, expired_at — when it opens}); ``/api/manga/<id--slug>
    /chapter?number=&volume=[&branch_id=]`` — its ``pages`` [{url «//manga/…», width, height}] on the picture server
    (``/api/constants?fields[]=imageServers``: «main» of site 1).  It has no votes: the translators with the most
    chapters are taken."""
    name = "mangalib"
    feed_order, catalog_order, legacy_search_order = 20, 20, 30
    catalog_kinds = ("manga", "manhwa")
    catalog_filters = True
    label = "MangaLib"
    hosts = ("mangalib.me", "mangalib.org", "test-front.mangalib.me")
    address_value, api_value = "MANGALIB_SITE", "MANGALIB_API"
    cover_hosts = ("imglib.info", "mangalib.me", "mangalib.org", "cdnlibs.org", "lib.social")
    # (v5.44.149) ``artists`` — asked 11.11.2026: [{id, slug_url, name, rus_name, model «people»}], as ``authors``
    FIELDS = ("summary", "genres", "tags", "authors", "artists", "otherNames", "releaseDate")
    SITE_ID = 1                                 # (v5.44.96) its site of the Lib sites' API (HentaiLib — 4)

    def site(self) -> str:
        return S.address(self.name) or S.MANGALIB_SITE

    def api(self) -> str:
        return S.address(self.name + "_api") or S.MANGALIB_API

    def token(self) -> str:
        return S.token_of(self.name)

    def images(self) -> str:
        return S.MANGALIB_IMAGES

    def headers(self) -> dict:
        out = {"Accept": "application/json", "Site-Id": str(self.SITE_ID), "Referer": self.site() + "/",
               "Origin": self.site()}
        token = self.token()
        if token:                               # (v5.44.78) the owner's sign-in: the titles 18+ it shows only so
            out["Authorization"] = f"Bearer {token}"
        return out

    def _never(self) -> str:
        """(v5.44.96) What its own lists are always asked without (``gates.left_out`` — «site»)."""
        left = G.left_out(self.name, "nsfw", where="site")
        tags, genres = left.tags, left.genres
        return "".join(f"&tags_exclude[]={n}" for n in tags) + "".join(f"&genres_exclude[]={n}" for n in genres)

    def key_of(self, url: str) -> str:
        if not S._ours(url, self.site(), self.hosts):
            return ""
        path = urlparse(url).path
        # (v5.44.63) a link may name the title by its slug alone — «/ru/manga/yeokdagp-yeongji-sulgyesa» (the owner's,
        # 4.11.2026); the API takes either and answers with its «slug_url» («89959--…»), the key from then on
        for pattern in (r"/(?:[a-z]{2}/)?manga/([^/?#]+)",              # /ru/manga/<id--slug or slug>
                        r"/(?:[a-z]{2}/)?(\d+--[^/?#]+)",                # /ru/<id--slug>/read/…
                        r"/(?:[a-z]{2}/)?([a-z0-9][\w-]*)/read(?:/|$)",  # /ru/<slug>/read/…
                        r"/([a-z0-9][\w-]*-[\w-]+)(?:/v[\d.]+/c[\d.]+)?/?$"):  # the old /<slug>, /<slug>/v1/c1
            m = re.match(pattern, path)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def _summary(doc) -> str:
        """The description: a document of paragraphs (or plain text, or HTML)."""
        if isinstance(doc, str):
            return S.plain(doc)
        out = []

        def walk(node, line):
            if isinstance(node, dict):
                kind = node.get("type")
                if kind == "text":
                    line.append(str(node.get("text") or ""))
                elif kind == "hardBreak":
                    line.append("\n")
                for child in node.get("content") or []:
                    walk(child, line)
            elif isinstance(node, list):
                for child in node:
                    walk(child, line)
        for block in (doc or {}).get("content") or [] if isinstance(doc, dict) else []:
            line: list = []
            walk(block, line)
            text = "".join(line).strip()
            if text:
                out.append(text)
        return "\n\n".join(out)

    def _chapters_raw(self, net: S.Net, key: str) -> list:
        """Its chapters as the API gave them — asked once for a look (the title's translators and its chapters)."""
        if (self.name, key) not in net.kept:
            data = net.json(f"{self.api()}/api/manga/{key}/chapters", what=self.label, headers=self.headers())
            net.kept[(self.name, key)] = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"),
                                                                                                   list) else []
        return net.kept[(self.name, key)]

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        fields = "&".join(f"fields[]={f}" for f in self.FIELDS)
        data = net.json(f"{self.api()}/api/manga/{key}?{fields}", what=self.label, headers=self.headers())
        c = data.get("data") if isinstance(data, dict) else None
        if not isinstance(c, dict) or not (c.get("rus_name") or c.get("name")):
            raise S.Missing(f"{self.label} не знает такого тайтла")
        name = str(c.get("rus_name") or c.get("name") or "").strip()
        alt = []
        for x in [c.get("eng_name"), c.get("name"), *(c.get("otherNames") or [])]:
            x = str(x or "").strip()
            if x and x != name and x not in alt:
                alt.append(x)
        label = lambda v: str(v.get("label") or v.get("name") or "") if isinstance(v, dict) else ""   # noqa: E731
        # (v5.44.143) its «18+ (RX)» is erotica (as «Лента» has it): MangaLib's own titles of hentai too
        erotic = self.adult_only or "RX" in label(c.get("ageRestriction")).upper()
        ids = lambda items: [x.get("id") for x in items or [] if isinstance(x, dict)]                # noqa: E731
        why = G.title(self.name, names=[name, *alt], tags=S._names(c.get("tags")), tag_ids=ids(c.get("tags")),
                      genres=S._names(c.get("genres")), genre_ids=ids(c.get("genres")), adult=erotic)
        if why:                                      # (v5.44.96) the gate — HentaiLib's titles, (v5.44.143) MangaLib's
            raise S.Excluded(why)                      # of «18+ (RX)» — before its chapters are asked
        year = S._year(c.get("releaseDate") or c.get("releaseDateString"))
        branches: dict = {}
        for ch in self._chapters_raw(net, str(c.get("slug_url") or key)):
            for b in ch.get("branches") or []:
                bid = str(b.get("branch_id") if b.get("branch_id") is not None else "0")
                entry = branches.setdefault(bid, S.Branch(id=bid, name=", ".join(S._names(b.get("teams")))))
                entry.count += 1
        cover = c.get("cover") if isinstance(c.get("cover"), dict) else {}
        return S.TitleRef(site=self.name, key=str(c.get("slug_url") or key),
                        url=f"{self.site()}/ru/manga/{c.get('slug_url') or key}", title=name, alt_titles=alt,
                        type=label(c.get("type")), status=label(c.get("status")), year=year,
                        age=label(c.get("ageRestriction")), is_erotic=erotic, genres=S._names(c.get("genres")),
                        tags=S._names(c.get("tags")), description=self._summary(c.get("summary")),
                        cover_url=str(cover.get("default") or cover.get("md") or ""),
                        authors=S._names(c.get("authors")), people=self._people(c), branches=list(branches.values()))

    @staticmethod
    def _people(c: dict) -> list:
        """(v5.44.149) [{id, name}] of the title's authors and artists (a person once)."""
        out, seen = [], set()
        for x in [*(c.get("authors") or []), *(c.get("artists") or [])]:
            number = S._int(x.get("id")) if isinstance(x, dict) else 0
            name = " ".join(str(x.get("name") or x.get("rus_name") or "").split()) if isinstance(x, dict) else ""
            if number > 0 and name and number not in seen:
                seen.add(number)
                out.append({"id": number, "name": name})
        return out

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        branch = branch or (title.best_branch().id if title.best_branch() else "0")
        out = []
        for ch in self._chapters_raw(net, title.key):
            for b in ch.get("branches") or []:
                bid = str(b.get("branch_id") if b.get("branch_id") is not None else "0")
                if bid != branch:
                    continue
                locked = b.get("restricted_view") if isinstance(b.get("restricted_view"), dict) else None
                paid = bool(locked) and not locked.get("is_open")
                volume, raw = str(ch.get("volume") or ""), str(ch.get("number") or "")
                number = S._num(raw)
                out.append(S.ChapterRef(id=f"{volume}/{raw}/{bid}", tome=S._int(volume), number=number,
                                      name=str(ch.get("name") or "").strip(), free=not paid,
                                      free_at=str(locked.get("expired_at") or "") if paid else "",
                                      date=str(b.get("created_at") or ""), index=S._int(ch.get("index")),
                                      url=f"{self.site()}/ru/{title.key}/read/v{volume}/c{number}"))
        out.sort(key=lambda c: (c.index, c.tome, S._number_key(c.number)))
        return out

    SERVERS = ("main", "secondary", "compress")      # (v5.44.82) the order its picture servers are asked in

    def _image_servers(self, net: S.Net) -> list:
        """(v5.44.82) Its picture servers for its site, «main» first, then «secondary», «compress» and the others (the
        friend's reader Yomi tries them in turn); none named — :data:`MANGALIB_IMAGES` (HentaiLib's —
        :data:`HENTAILIB_IMAGES`)."""
        if (self.name, "servers") not in net.kept:
            found = []
            try:
                data = net.json(f"{self.api()}/api/constants?fields[]=imageServers", what=self.label,
                                headers=self.headers())
                servers = ((data.get("data") or {}).get("imageServers") or []) if isinstance(data, dict) else []
                mine = [s for s in servers if isinstance(s, dict) and s.get("url") and
                        self.SITE_ID in [S._int(x) for x in (s.get("site_ids") or [])]]
                mine.sort(key=lambda s: self.SERVERS.index(s.get("id")) if s.get("id") in self.SERVERS else 9)
                for server in mine:
                    link = str(server["url"]).rstrip("/")
                    if link not in found:
                        found.append(link)
            except S.SourceError as exc:
                S._LOG.info("%s's picture servers not asked: %s", self.label, exc)
            net.kept[(self.name, "servers")] = found or [self.images()]
        return net.kept[(self.name, "servers")]

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        if not chapter.free:
            raise S.Paid("глава платная — её программа не скачивает")
        volume, number, bid = (chapter.id.split("/") + ["", "", "0"])[:3]
        query = f"number={number}&volume={volume}" + (f"&branch_id={bid}" if bid and bid != "0" else "")
        try:
            data = net.json(f"{self.api()}/api/manga/{title.key}/chapter?{query}", what=self.label,
                            headers=self.headers())
        except S.Missing as exc:
            if self.SIGNED_ONLY and not self.signed_in():   # (v5.44.96) HentaiLib gives pages to a sign-in alone
                raise S.Closed(self.sign_in_words()) from exc
            raise
        c = data.get("data") if isinstance(data, dict) else None
        if not isinstance(c, dict):
            raise S.SourceError(f"{self.label} не дал главу")
        locked = c.get("restricted_view") if isinstance(c.get("restricted_view"), dict) else None
        if locked and not locked.get("is_open"):
            raise S.Paid("глава платная — её программа не скачивает")
        servers = self._image_servers(net)
        out = []
        for p in c.get("pages") or []:
            if isinstance(p, dict) and p.get("url"):
                link = str(p["url"])
                if link.startswith("http"):
                    links = [link]
                else:                                       # «//manga/…»: one slash; (v5.44.82) every server of it
                    links = [server + "/" + link.lstrip("/") for server in servers]
                out.append(S.Picture(url=links[0], width=S._int(p.get("width")), height=S._int(p.get("height")),
                                   fallbacks=links[1:]))
        if not out:
            raise S.SourceError(f"в главе на {self.label} нет страниц")
        return out

    SIGNED_ONLY = False                         # (v5.44.96) a chapter's pages to a sign-in alone (HentaiLib)

    def signed_in(self) -> bool:                # (v5.44.99) its token (MangaLib's for HentaiLib — one account)
        return bool(self.token())

    def search(self, net: S.Net, query: str) -> list:
        data = net.json(f"{self.api()}/api/manga?q={quote(query)}&site_id[]={self.SITE_ID}{self._never()}",
                        what=self.label, headers=self.headers())
        return self._hits(data)

    FEED_ORDER = {"new": "last_chapter_at", "popular": "views"}

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        data = net.json(f"{self.api()}/api/manga?site_id[]={self.SITE_ID}&page={max(1, page)}&sort_by="
                        f"{self.FEED_ORDER.get(how, 'last_chapter_at')}&sort_type=desc{self._never()}", what=self.label,
                        headers=self.headers())
        meta = data.get("meta") if isinstance(data, dict) and isinstance(data.get("meta"), dict) else {}
        hits = self._hits(data)
        return hits, bool(meta.get("has_next_page")) if "has_next_page" in meta else len(hits) >= 20

    def _hits(self, data) -> list:
        out = []
        for x in (data.get("data") if isinstance(data, dict) else None) or []:
            if not isinstance(x, dict) or not x.get("slug_url"):
                continue
            year = S._year(x.get("releaseDate") or x.get("releaseDateString"))
            names = [str(n).strip() for n in (x.get("rus_name"), x.get("eng_name"), x.get("name")) if n and str(n).strip()]
            age = x.get("ageRestriction") if isinstance(x.get("ageRestriction"), dict) else {}
            erotic = self.adult_only or "RX" in str(age.get("label") or "").upper()     # (v5.44.143) as ``title``
            if G.title(self.name, names=names, adult=erotic):
                continue                             # (v5.44.96) a gate closed it — by the name too (HentaiLib)
            cover = x.get("cover") if isinstance(x.get("cover"), dict) else {}
            rating = x.get("rating") if isinstance(x.get("rating"), dict) else {}
            out.append(S.Hit(site=self.name, key=str(x["slug_url"]), names=names,
                           type=str((x.get("type") or {}).get("label") or "") if isinstance(x.get("type"), dict) else "",
                           year=year, url=f"{self.site()}/ru/manga/{x['slug_url']}",
                           cover=str(cover.get("thumbnail") or cover.get("default") or cover.get("md") or ""),
                           rating=S._float(rating.get("average")), votes=S._int(rating.get("votes")),
                           adult=erotic))
        return out

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"


class HentaiLib(MangaLib):
    """(v5.44.96) HentaiLib (hentailib.me) — the owner (6.11.2026), asked whether to take it through his sign-in of
    MangaLib: «Ну дак делай».  The API of MangaLib (``api.cdnlibs.org``) with ``Site-Id: 4`` (asked from the owner's
    computer 6.11.2026): its list (60 a page), a title and its chapters — without a sign-in; a chapter's pages — 404
    without one: :data:`SIGNED_ONLY` — its own sign-in, else MangaLib's (one account of the Lib sites); its pictures —
    its own servers of ``/api/constants`` (site 4: img2h/img3h.hentaicdn.org).  Every title is «18+ (RX)»: a site of
    hentai alone.  The program's gates: what ``gates.left_out`` names is always left out of its lists; a title
    ``gates.title`` closes (by its tags, genres or name) — not a hit, and :class:`Excluded` when opened."""
    name = "hentailib"
    feed_order, catalog_order, legacy_search_order = 70, 30, 0
    label = "HentaiLib"
    hosts = ("hentailib.me", "hentailib.org")
    address_value, api_value = "HENTAILIB_SITE", "HENTAILIB_API"
    adult_only = True
    SITE_ID = 4
    SIGNED_ONLY = True

    def site(self) -> str:
        return S.address(self.name) or S.HENTAILIB_SITE

    def api(self) -> str:                      # the owner's address of MangaLib's API serves it too
        return S.address(self.name + "_api") or S.address("mangalib_api") or S.HENTAILIB_API

    def images(self) -> str:
        return S.HENTAILIB_IMAGES

    def token(self) -> str:                    # its own sign-in, else MangaLib's: one account of the Lib sites
        return S.token_of(self.name) or S.token_of("mangalib")
