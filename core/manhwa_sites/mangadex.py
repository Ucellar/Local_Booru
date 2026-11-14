"""(v5.44.168) MangaDex — of the sites of ``core/manhwa_sources.py``: its API: the title, the feed of its chapters by
language, a chapter's servers.

Taken out of ``core/manhwa_sources.py`` as it was (that file was 3 700 lines — every site in one).  What a site is
(``Source``), the errors, the requests (``Net``) and the names the sites share stay there; this module asks them as
``S.<name>`` when it runs — a site's address that a check or «Адреса сайтов и вход» puts there is the one asked.
``manhwa_sources`` itself imports it, and its classes are that module's names as before
(``manhwa_sources.MangaDex``).
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from core import manhwa_sources as S


class MangaDex(S.Source):
    """(v5.44.79) MangaDex — a title in every language it is translated into; the owner (5.11.2026, of his friend's
    reader): «искало именно того перевода который тебе надо или оригинал».  Its open API (``api.mangadex.org``; from
    the owner's computer it first sends every request to its own check for browsers — ``mangadex.org/edge/challenge``:
    the program window's browser goes through it as through com-x.life's, ``Net``): ``/manga/<id>?includes[]=…`` —
    the title (``title``/``altTitles`` in many languages, ``description``, ``originalLanguage``, ``status``,
    ``year``, ``contentRating``, ``tags``, ``availableTranslatedLanguages``, the cover's file —
    ``uploads.mangadex.org/covers/<id>/<file>``); its «translators» here are languages (:data:`WANTED`: Russian,
    English, the original one), each a branch with its chapters counted by ``/manga/<id>/aggregate?
    translatedLanguage[]=``; the chapters of one — ``/manga/<id>/feed?translatedLanguage[]=…`` page by page (500): a
    chapter of several groups — of the group with the most chapters, one hosted elsewhere (``externalUrl``) or
    without pages — not taken; a chapter's pictures — ``/at-home/server/<chapter id>`` → ``<baseUrl>/data/<hash>/
    <file>``.  The language taken unless the card picks another — «Настройки → Манга и манхва» (:data:`LANGUAGE`)."""
    name = "mangadex"
    feed_order = 60
    label = "MangaDex"
    hosts = ("mangadex.org",)
    address_value, api_value = "MANGADEX_SITE", "MANGADEX_API"
    cover_hosts = ("mangadex.org",)
    by_name = False                   # (v5.44.79) its own check for browsers first: only by a link
    languages = True
    WANTED = ("ru", "en")
    RATINGS = "&".join(f"contentRating[]={r}" for r in ("safe", "suggestive", "erotica", "pornographic"))
    KINDS = {"ja": "Манга", "ko": "Манхва", "zh": "Маньхуа", "zh-hk": "Маньхуа", "en": "Комикс"}
    NAMES = {"ru": "Русский", "en": "English", "ja": "японский", "ko": "корейский", "zh": "китайский",
             "zh-hk": "китайский", "id": "индонезийский", "fr": "французский", "es": "испанский"}
    STATUSES = {"ongoing": "Продолжается", "completed": "Завершён", "hiatus": "Приостановлен",
                "cancelled": "Выпуск прекращён"}
    AGES = {"suggestive": "16+", "erotica": "18+", "pornographic": "18+"}
    # (v5.44.143) its ratings of erotica: «erotica» (sexual, not explicit) is one too — before, «pornographic» alone
    EROTIC = ("erotica", "pornographic")
    UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    PAGE = 500

    def site(self) -> str:
        return S.address(self.name) or S.MANGADEX_SITE

    def api(self) -> str:
        return S.address(self.name + "_api") or S._api_of(S.address(self.name)) or S.MANGADEX_API

    def headers(self) -> dict:
        return {"Accept": "application/json", "Referer": self.site() + "/"}

    def key_of(self, url: str) -> str:
        if not S._host_of(url, self.hosts, self.site()):
            return ""
        m = re.match(r"/(?:title|manga)/(" + self.UUID + r")(?:/|$)", urlparse(url).path, re.I)
        return m.group(1).lower() if m else ""

    def title_of_chapter(self, net: S.Net, chapter: str) -> str:
        """(v5.44.81) The link of the title of one of its chapters (``chapter`` — the chapter's id: SauceNAO's answers,
        ComicInfo.xml of «Из архива» name chapters): ``/chapter/<id>`` → its relation «manga»; "" — none."""
        if not re.fullmatch(self.UUID, str(chapter or ""), re.I):
            return ""
        data = net.json(f"{self.api()}/chapter/{str(chapter).lower()}", what=self.label, headers=self.headers())
        body = data.get("data") if isinstance(data, dict) else None
        relations = body.get("relationships") if isinstance(body, dict) else None
        manga = next((str(r.get("id") or "") for r in relations or [] if isinstance(r, dict) and
                      r.get("type") == "manga"), "")
        return f"{self.site()}/title/{manga.lower()}" if re.fullmatch(self.UUID, manga, re.I) else ""

    @staticmethod
    def _in(names, *order) -> str:
        """The name of ``names`` ({language: name}) in the first of ``order`` it has, else any."""
        names = names if isinstance(names, dict) else {}
        for lang in order:
            if str(names.get(lang) or "").strip():
                return str(names[lang]).strip()
        return next((str(v).strip() for v in names.values() if str(v or "").strip()), "")

    def _cover(self, key: str, relations: list, size: str = "") -> str:
        art = next((r for r in relations if isinstance(r, dict) and r.get("type") == "cover_art"), None)
        name = str(((art or {}).get("attributes") or {}).get("fileName") or "")
        return f"{_cover_host(self)}/covers/{key}/{name}{size}" if name else ""

    def _count(self, net: S.Net, key: str, lang: str) -> int:
        data = net.json(f"{self.api()}/manga/{key}/aggregate?translatedLanguage[]={lang}", what=self.label,
                        headers=self.headers())
        volumes = data.get("volumes") if isinstance(data, dict) else None
        found = set()
        for volume in (volumes.values() if isinstance(volumes, dict) else volumes or []):
            chapters = volume.get("chapters") if isinstance(volume, dict) else None
            for number in (chapters.keys() if isinstance(chapters, dict) else []):
                if number and number != "none":
                    found.add(number)
        return len(found)

    def title(self, net: S.Net, key: str) -> S.TitleRef:
        key = str(key).lower()
        data = net.json(f"{self.api()}/manga/{key}?includes[]=cover_art&includes[]=author&includes[]=artist",
                        what=self.label, headers=self.headers())
        item = data.get("data") if isinstance(data, dict) else None
        attrs = item.get("attributes") if isinstance(item, dict) else None
        if not isinstance(attrs, dict):
            raise S.Missing(f"{self.label} не знает такого тайтла")
        relations = item.get("relationships") if isinstance(item.get("relationships"), list) else []
        alts = [a for a in attrs.get("altTitles") or [] if isinstance(a, dict)]
        russian = next((str(a["ru"]).strip() for a in alts if str(a.get("ru") or "").strip()), "")
        main = russian or self._in(attrs.get("title"), "en", "ja-ro", "ko-ro", "zh-ro")
        names = [self._in(attrs.get("title"), "en", "ja-ro")] + [str(v).strip() for a in alts for v in a.values()]
        alt = []
        for name in names:
            if name and name != main and name not in alt:
                alt.append(name)
        original = str(attrs.get("originalLanguage") or "")
        available = [str(x) for x in attrs.get("availableTranslatedLanguages") or [] if x]
        wanted = S.LANGUAGE[0]
        rating = str(attrs.get("contentRating") or "")
        tags = [t for t in attrs.get("tags") or [] if isinstance(t, dict)]
        tag_name = lambda t: self._in((t.get("attributes") or {}).get("name"), "ru", "en")       # noqa: E731
        ref = S.checked(S.TitleRef(          # (v5.44.143) the program's gate — by a link too, before its
            site=self.name, key=key, url=f"{self.site()}/title/{key}", title=main, alt_titles=alt,   # chapters are
            type=self.KINDS.get(original, ""), status=self.STATUSES.get(str(attrs.get("status") or ""), ""),  # asked
            year=S._int(attrs.get("year")), age=self.AGES.get(rating, ""), is_erotic=rating in self.EROTIC,
            genres=[tag_name(t) for t in tags if (t.get("attributes") or {}).get("group") in ("genre", "theme")],
            tags=[tag_name(t) for t in tags if (t.get("attributes") or {}).get("group") not in ("genre", "theme")],
            description=S.plain(self._in(attrs.get("description"), "ru", "en")),
            cover_url=self._cover(key, relations, ".512.jpg"),
            authors=list(dict.fromkeys(str((r.get("attributes") or {}).get("name") or "") for r in relations
                                       if isinstance(r, dict) and r.get("type") in ("author", "artist")
                                       and (r.get("attributes") or {}).get("name"))),
            original=original, prefer=original if wanted == "original" else wanted))
        for lang in dict.fromkeys([*self.WANTED, original]):
            if lang and lang in available:
                label = self.NAMES.get(lang, lang)
                name = f"{label} — оригинал" if lang == original else label
                ref.branches.append(S.Branch(id=lang, name=name, count=self._count(net, key, lang)))
        return ref

    def chapters(self, net: S.Net, title: S.TitleRef, branch: str = "") -> list:
        lang = branch or (title.best_branch().id if title.best_branch() else "")
        if not lang:
            return []
        rows, offset = [], 0
        while offset < 10000:
            data = net.json(f"{self.api()}/manga/{title.key}/feed?translatedLanguage[]={lang}&limit={self.PAGE}"
                            f"&offset={offset}&order[volume]=asc&order[chapter]=asc&includes[]=scanlation_group"
                            f"&{self.RATINGS}", what=self.label, headers=self.headers())
            items = data.get("data") if isinstance(data, dict) else None
            rows += [x for x in items or [] if isinstance(x, dict)]
            offset += self.PAGE
            if not items or offset >= S._int(data.get("total")):
                break
        groups: dict = {}
        usable = []
        for x in rows:
            attrs = x.get("attributes") if isinstance(x.get("attributes"), dict) else {}
            if attrs.get("externalUrl") or not S._int(attrs.get("pages")) or attrs.get("chapter") in (None, ""):
                continue                               # hosted elsewhere, no pages, no number
            group = next((r for r in x.get("relationships") or [] if isinstance(r, dict) and
                          r.get("type") == "scanlation_group"), {})
            name = str((group.get("attributes") or {}).get("name") or "")
            groups[name] = groups.get(name, 0) + 1
            usable.append((x, attrs, name))
        found: dict = {}
        for x, attrs, name in usable:
            ch = S.ChapterRef(id=str(x.get("id")), tome=S._int(attrs.get("volume")), number=S._num(attrs.get("chapter")),
                            name=str(attrs.get("title") or "").strip(), date=str(attrs.get("publishAt") or ""),
                            url=f"{self.site()}/chapter/{x.get('id')}")
            old = found.get(ch.number)
            if old is None or groups.get(name, 0) > groups.get(old[1], 0):
                found[ch.number] = (ch, name)       # one chapter of several groups: of the one with the most
        out = sorted((ch for ch, _name in found.values()), key=lambda c: (c.tome, S._number_key(c.number)))
        for n, ch in enumerate(out, 1):
            ch.index = n
        return out

    def pictures(self, net: S.Net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        data = net.json(f"{self.api()}/at-home/server/{chapter.id}", what=self.label, headers=self.headers())
        base = str(data.get("baseUrl") or "") if isinstance(data, dict) else ""
        holder = data.get("chapter") if isinstance(data, dict) and isinstance(data.get("chapter"), dict) else {}
        files = [str(f) for f in holder.get("data") or [] if f]
        if not base or not holder.get("hash") or not files:
            raise S.SourceError(f"в главе на {self.label} нет страниц")
        return [S.Picture(url=f"{base.rstrip('/')}/data/{holder['hash']}/{f}") for f in files]

    def search(self, net: S.Net, query: str) -> list:
        data = net.json(f"{self.api()}/manga?title={quote(query)}&limit=10&includes[]=cover_art&order[relevance]=desc"
                        f"&{self.RATINGS}", what=self.label, headers=self.headers())
        return self._hits(data)

    FEED_ORDER = {"new": "latestUploadedChapter", "popular": "followedCount"}
    FEED_COUNT = 30

    def feed(self, net: S.Net, how: str, page: int) -> tuple:
        """Its titles with chapters in the language chosen («Язык глав MangaDex»; «оригинал» — Russian)."""
        language = S.LANGUAGE[0] if S.LANGUAGE and S.LANGUAGE[0] in ("ru", "en") else "ru"
        offset = self.FEED_COUNT * (max(1, page) - 1)
        data = net.json(f"{self.api()}/manga?limit={self.FEED_COUNT}&offset={offset}&includes[]=cover_art"
                        f"&availableTranslatedLanguage[]={language}&hasAvailableChapters=true&{self.RATINGS}"
                        f"&order[{self.FEED_ORDER.get(how, 'latestUploadedChapter')}]=desc", what=self.label,
                        headers=self.headers())
        hits = self._hits(data)
        total = S._int(data.get("total")) if isinstance(data, dict) else 0
        return hits, offset + len(hits) < total

    def _hits(self, data) -> list:
        out = []
        for item in (data.get("data") if isinstance(data, dict) else None) or []:
            attrs = item.get("attributes") if isinstance(item, dict) else None
            if not isinstance(attrs, dict) or not item.get("id"):
                continue
            names = [self._in(attrs.get("title"), "en", "ja-ro")] + [
                str(v).strip() for a in attrs.get("altTitles") or [] if isinstance(a, dict) for v in a.values()]
            key = str(item["id"]).lower()
            out.append(S.Hit(site=self.name, key=key, names=list(dict.fromkeys(n for n in names if n)),
                           type=self.KINDS.get(str(attrs.get("originalLanguage") or ""), ""), year=S._int(attrs.get("year")),
                           url=f"{self.site()}/title/{key}",
                           cover=self._cover(key, item.get("relationships") or [], ".256.jpg"),
                           adult=attrs.get("contentRating") in self.EROTIC))
        return out

    def referer(self, title: S.TitleRef) -> str:
        return self.site() + "/"


def _cover_host(source: "MangaDex") -> str:
    """MangaDex's covers: «uploads.» + its address (the checks' own site — itself)."""
    site = urlparse(source.site())
    host = site.netloc.lower()
    if host.endswith("mangadex.org") and not host.startswith("uploads."):
        return f"{site.scheme}://uploads.{host.removeprefix('www.')}"
    return f"{site.scheme}://{host}"
