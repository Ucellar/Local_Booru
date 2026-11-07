"""The description of a series from its page on a site: title, other titles, year, genres, studio, who voiced it,
the text, the poster — what «Описание с сайта» on a series page takes (``core/video_meta_import.py`` gets the page).

Two kinds of markup are read by their shape, not by the site's address (a mirror under another name works too):

* DLE sites such as hentasis: ``<h1>Русское / Romaji (2026г.)</h1>``, the poster ``.f-mov-img img``, the lines
  ``ul.mov-lines li`` («Год выпуска:», «Жанр:», «Русская озвучка:», «Примечание:» — who voiced which file, «Описание:»),
  the player's list of files (``new Playerjs({file:[{file:"…mp4", title:"Файл 1"}, …]})``);
* YummyAnime (old.yummyani.me): ``h1[itemprop=name]``, ``.alt-names-list``, the poster ``img[data-full]``, the lines
  ``ul.content-main-info li`` (Статус, Тип, Год, Жанры, Студия, Режиссёр, Серии, Перевод), the voice-overs
  ``ul.animeVoices``, the ratings ``.content-ref-ids``, the text ``#content-desc-text``.

Any other page gives what it has the usual way: Open Graph and JSON-LD, ``<h1>``, and lines «Название поля: значение»
whose name is one of :data:`FIELD_LABELS` (in Russian or English) — also with a word about what it is after it
(«Описание хентая:», «Год выпуска аниме:» — :data:`SUBJECTS`), and a line that holds only its name gets the text after
it («<p><b>Описание:</b></p><p>Текст…</p>», hentai-xs).  A description the site cut for its meta tag («…улетел в.»)
is taken whole from the page when the page has it.  The poster of such a page: the picture beside these lines (not one
inside a link to another title, not an icon), pictures whose ``alt`` is the title, and a picture named by the title's
number in the page's address (``anime.php?id=760`` → ``…/760.webp``).

What is returned is the content of ``info.json`` (see :data:`FIELD_ORDER`), plus ``poster_urls`` — where the poster
may be, best first — which the importer takes away.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urljoin, urlparse

FORMAT = "local-booru-video-1"

# field -> the names a site gives it (lower case, «ё» as «е», without the colon)
FIELD_LABELS = {
    "year": ("год выпуска", "год производства", "год выхода", "год создания", "год премьеры", "год релиза", "год",
             "дата выхода", "дата выпуска", "дата премьеры", "выпуск", "release year", "production year", "year",
             "premiered", "aired", "released"),
    "season": ("сезон", "season"),
    "type": ("тип", "формат", "type", "format"),
    "status": ("статус", "status"),
    "genres": ("жанр", "жанры", "genre", "genres"),
    "studio": ("студия", "студии", "studio", "studios"),
    "director": ("режиссер", "режиссеры", "director", "directors"),
    "episodes": ("эпизоды", "количество серий", "кол-во серий", "серии", "серий", "эпизодов", "episodes"),
    "duration": ("продолжительность", "длительность", "duration"),
    "voice": ("озвучка", "озвучивание", "озвучили", "voice actors", "voice acting", "dubbing", "dub"),
    "language": ("язык", "язык озвучки", "языки", "language", "audio language", "languages"),
    "russian_voice": ("русская озвучка",),
    "translation": ("перевод", "translation", "translations"),
    "subtitles": ("русские субтитры", "субтитры", "subtitles"),
    "censorship": ("цензура", "censorship"),
    "age": ("возрастное ограничение", "возрастной рейтинг", "возраст", "age rating", "рейтинг mpaa"),
    "source": ("первоисточник", "source"),
    "country": ("страна", "country"),
    "description": ("описание", "краткое описание", "полное описание", "сюжет", "description", "synopsis", "plot",
                    "storyline"),
    "notes": ("примечание", "примечания", "изменения"),
}
FIELD_TITLES = {
    "year": "Год", "season": "Сезон", "type": "Тип", "status": "Статус", "genres": "Жанры", "studio": "Студия",
    "director": "Режиссёр", "episodes": "Серий", "duration": "Длительность", "voice": "Озвучка", "language": "Язык",
    "russian_voice": "Русская озвучка", "translation": "Перевод", "subtitles": "Субтитры", "censorship": "Цензура",
    "age": "Возраст", "source": "Первоисточник", "country": "Страна", "notes": "Примечание",
}
LIST_FIELDS = {"genres", "studio", "director", "voice", "translation", "notes"}
# the order of the lines on the series page (description, title and tags have their own places)
FIELD_ORDER = ("year", "season", "type", "status", "genres", "studio", "director", "episodes", "duration", "voice",
               "language", "russian_voice", "translation", "subtitles", "censorship", "age", "source", "country", "notes")
_BY_LABEL = {label: key for key, labels in FIELD_LABELS.items() for label in labels}
# what a site may say after the name of a line: «Описание хентая:», «Жанр аниме:», «Год выпуска сериала:»
SUBJECTS = {"хентая", "хентай", "аниме", "сериала", "фильма", "мультфильма", "мультсериала", "тайтла", "релиза", "ова",
            "ova", "ona", "дорамы", "серии", "anime", "hentai", "series", "movie", "show", "title"}
_MORE = {"more...", "more…", "ещё...", "еще...", "ещё…", "больше...", "…", "...", "показать все", "show more"}
MAX_TEXT = 6000                       # characters of a description kept
MAX_EXTRA = 24


def _norm_label(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip().lower().replace("ё", "е")
    return text.rstrip(":：").strip()


def key_of(norm: str) -> str | None:
    """The field a line's name (``_norm_label``) is about: «описание хентая» — "description"; None — not one of ours."""
    key = _BY_LABEL.get(norm)
    if key is not None:
        return key
    words = norm.split(" ")
    for cut in (1, 2):
        if len(words) > cut and all(word in SUBJECTS for word in words[-cut:]):
            key = _BY_LABEL.get(" ".join(words[:-cut]))
            if key is not None:
                return key
    return None


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _items(node, *, skip=None) -> list[str]:
    """The names inside a value: its links or list items (not «More…»)."""
    out = []
    for el in node.find_all(["a", "li"]):
        if skip is not None and (el is skip or skip in el.parents):
            continue
        if "more-alt-names" in (el.get("class") or []):
            continue
        if el.name == "li" and el.find(["a", "li"]):
            continue
        text = _clean(el.get_text(" ", strip=True))
        if text and text.lower() not in _MORE and text not in out:
            out.append(text)
    return out


def _split(value: str) -> list[str]:
    parts = [p.strip(" .;") for p in re.split(r"\s*[,;]\s*", value or "")]
    return [p for p in parts if p]


class _Meta:
    """What was found so far; the first find of a field wins (the site's own markup is read first)."""

    def __init__(self):
        self.data: dict = {}
        self.extra: list = []
        self.posters: list = []
        self.lines: list = []          # elements of the lines «Поле: значение» found (where the poster may be beside)
        self.ld_description = ""       # the text of the page's structured data (JSON-LD): often cut by the site

    def put(self, key: str, value) -> None:
        if key in self.data and self.data[key]:
            return
        if isinstance(value, list):
            value = [_clean(v) for v in value if _clean(v)]
            value = list(dict.fromkeys(value))
            if not value:
                return
            if key not in LIST_FIELDS and key != "alt_titles" and key != "tags":
                value = ", ".join(value)
        else:
            value = _clean(value) if key != "description" else _text(value)
            if not value:
                return
            if key in LIST_FIELDS and key != "notes":
                value = _split(value)
            elif key == "notes":
                value = [value]
            elif key == "year":                         # «2026г.», «2026 г.» (hfshare) — the year
                value = re.sub(r"^((?:19|20)\d{2})\s*г?\.?$", r"\1", value)
        self.data[key] = value

    def labeled(self, label: str, value_text: str, items: list[str]) -> None:
        norm = _norm_label(label)
        key = key_of(norm)
        if key is None:
            for name, field_key in _BY_LABEL.items():          # «Возрастное ограничение 18+» has no colon
                if norm.startswith(name + " ") and len(norm) - len(name) <= 12:
                    key, value_text, items = field_key, norm[len(name):].strip(), []
                    break
        if key is None:
            label = _clean(label).rstrip(":")
            if label and value_text and len(self.extra) < MAX_EXTRA and len(value_text) <= 300 and len(label) <= 40:
                if label.lower() not in {x[0].lower() for x in self.extra}:
                    self.extra.append([label, _clean(value_text)])
            return
        if key == "notes":
            notes = self.data.setdefault("notes", [])
            text = _clean(value_text)
            if text and text not in notes:
                notes.append(text)
            return
        if key in LIST_FIELDS and items:
            self.put(key, items)
        else:
            self.put(key, value_text)

    def poster(self, url) -> None:
        url = str(url or "").strip()
        if url and not url.startswith("data:") and url not in self.posters:
            self.posters.append(url)


_TEXT_BLOCKS = {"p", "div", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section",
                "article", "header", "footer", "table", "tr", "pre"}


def block_text(el) -> str:
    """The text of an element as it reads: a line break for ``<br>`` and between blocks (paragraphs), none around
    inline elements («Второй <i>абзац</i>.» is one line)."""
    from bs4.element import NavigableString, PreformattedString
    if getattr(el, "name", None) is None:
        return _clean(str(el or ""))
    parts: list[str] = []

    def walk(node) -> None:
        for child in node.children:
            name = getattr(child, "name", None)
            if name is None:
                if isinstance(child, NavigableString) and not isinstance(child, PreformattedString):
                    parts.append(str(child))
            elif name in ("script", "style", "noscript", "template"):
                continue
            elif name == "br":
                parts.append("\n")
            elif name in _TEXT_BLOCKS:
                parts.append("\n")
                walk(child)
                parts.append("\n")
            else:
                walk(child)

    walk(el)
    lines = [re.sub(r"[ \t\f\v\xa0]+", " ", line).strip() for line in "".join(parts).split("\n")]
    return "\n".join(line for line in lines if line)


def _text(value) -> str:
    """A description: paragraphs kept, other white space folded, at most :data:`MAX_TEXT` characters."""
    text = str(value or "").replace("\r", "")
    paragraphs = [re.sub(r"[ \t\f\v]+", " ", p).strip() for p in re.split(r"\n\s*\n|\n", text)]
    text = "\n\n".join(p for p in paragraphs if p)
    return text[:MAX_TEXT].rstrip() + ("…" if len(text) > MAX_TEXT else "")


# ── the page ──────────────────────────────────────────────────────────────────
def parse_page(html: str, page_url: str = "") -> dict:
    soup = _soup(html)
    meta = _Meta()
    tags = {}
    for el in soup.find_all("meta"):
        name = str(el.get("property") or el.get("name") or el.get("itemprop") or "").strip().lower()
        if name and el.get("content") is not None and name not in tags:
            tags[name] = str(el.get("content"))
    canonical = soup.find("link", rel=lambda r: r and "canonical" in (r if isinstance(r, list) else [r]))
    url = _page_url(page_url, str(canonical.get("href") or "") if canonical else "", tags.get("og:url", ""))
    for key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src", "image"):
        if tags.get(key):
            meta.poster(tags[key])          # the site's own choice first; the importer keeps the largest it got

    _dle(soup, meta)
    _yummy(soup, meta)
    _json_ld(soup, meta)
    _generic_rows(soup, meta)
    _chip_rows(soup, meta)
    _title(soup, meta, tags)
    _alt_titles(soup, meta)
    _site_rating(soup, meta, tags, url or page_url)
    if not meta.data.get("description"):
        meta.put("description", _page_description(soup, meta, tags))
    for img in soup.select("[class*=poster] img, .f-mov-img img, img[itemprop=image], img[itemprop=contentUrl]"):
        meta.poster(img.get("data-full") or img.get("data-src") or img.get("src"))
    for link in (_pictures_beside(meta.lines) + _pictures_named(soup, str(meta.data.get("title") or ""))
                 + _pictures_by_number(soup, url or page_url)):
        meta.poster(link)
    files = _player_files(html)
    if files:
        _file_notes(files, meta.data.get("notes") or [])
        meta.data["files"] = files
    ids = page_ids(soup)
    if ids:
        meta.data["ids"] = ids
    out = {"format": FORMAT}
    out.update(meta.data)
    if meta.extra:
        out["extra"] = meta.extra
    if url:
        out["source_url"] = url
        out["site"] = (urlparse(url).hostname or "").removeprefix("www.")
    # as written on the page: a relative address is a file next to a saved page, or a file of the site
    # (``poster_address``); the importer tries both
    out["poster_urls"] = ["https:" + p if p.startswith("//") else p for p in meta.posters]
    if "alt_titles" in out:
        out["alt_titles"] = [t for t in out["alt_titles"] if t.lower() != str(out.get("title") or "").lower()]
        if not out["alt_titles"]:
            out.pop("alt_titles")
    return out


_ID_LINKS = (
    ("shikimori", re.compile(r"(?:^|\.)(?:shikimori|shiki)\.[a-z]{2,10}$"),
     re.compile(r"^/animes/[a-z]{0,2}(\d{1,9})(?:[-/]|$)")),
    ("mal", re.compile(r"(?:^|\.)myanimelist\.net$"), re.compile(r"^/anime/(\d{1,9})(?:[/?]|$)")),
)


def page_ids(soup) -> dict:
    """{"shikimori": 52991, "mal": 52991} — the title's numbers in the anime databases the page links to (YummyAnime:
    the block of ratings ``.content-ref-ids``, else any link of the page).  A database linked to with two different
    numbers names no number: such a page lists other titles too."""
    links = soup.select(".content-ref-ids a[href]") or soup.find_all("a", href=True)
    found: dict = {}
    for a in links:
        href = str(a.get("href") or "").strip()
        try:
            parsed = urlparse("https:" + href if href.startswith("//") else href)
            host = (parsed.hostname or "").lower().removeprefix("www.")
        except ValueError:                           # «http://[broken»: not a link to anything
            continue
        for key, host_re, path_re in _ID_LINKS:
            number = path_re.match(parsed.path or "") if host_re.search(host) else None
            if number:
                found.setdefault(key, set()).add(int(number.group(1)))
    return {key: next(iter(numbers)) for key, numbers in found.items() if len(numbers) == 1}


def _page_url(given: str, canonical: str, og_url: str) -> str:
    for candidate in (canonical, og_url):
        if candidate.startswith(("http://", "https://")):
            return candidate
    if given.startswith(("http://", "https://")):
        return urljoin(given, og_url or canonical) if (og_url or canonical) else given
    return ""


def poster_address(link: str, base: str) -> str:
    """The web address of a poster written on a page whose address is ``base`` ("" when there is none)."""
    link = str(link or "").strip()
    if link.startswith("//"):
        return "https:" + link
    if link.startswith(("http://", "https://")):
        return link
    return urljoin(base, link) if base.startswith(("http://", "https://")) else ""


def _dle(soup, meta: _Meta) -> None:
    """DLE: «ul.mov-lines li» with «.mov-label»."""
    for li in soup.select("ul.mov-lines li, .mov-list li, ul.flist li"):
        label_el = li.select_one(".mov-label") or li.find(["b", "strong"])
        if label_el is None:
            continue
        label = label_el.get_text(" ", strip=True)
        whole = li.get_text(" ", strip=True)
        value = whole[len(label):] if whole.startswith(label) else whole.replace(label, "", 1)
        value_el = li.select_one(".mov-desc")
        if value_el is not None and _norm_label(label) in ("описание", "description"):
            value = block_text(value_el)
        meta.labeled(label, value.strip(" :"), _items(li, skip=label_el))
    for img in soup.select(".f-mov-img img, .fposter img, .full-poster img"):
        meta.poster(img.get("data-src") or img.get("src"))
    tags = [_clean(a.get_text(" ", strip=True)) for a in soup.select(".short-bottom a, .full-tags a")]
    if tags:
        meta.put("tags", tags)


def _yummy(soup, meta: _Meta) -> None:
    """YummyAnime: «ul.content-main-info», «ul.animeVoices», «.alt-names-list», «.content-ref-ids»."""
    for li in soup.select("ul.content-main-info > li"):
        label_el = li.find("span", recursive=False) or li.find("span")
        if label_el is None:
            continue
        label = label_el.get_text(" ", strip=True)
        if not label.endswith(":") and not label.endswith("："):
            continue
        whole = li.get_text(" ", strip=True)
        value = whole[len(label):] if whole.startswith(label) else whole
        meta.labeled(label, value.strip(), _items(li, skip=label_el))
    voices = [_clean(a.get_text(" ", strip=True)) for a in soup.select("ul.animeVoices a")]
    if voices:
        meta.put("voice", voices)
    alt = [_clean(li.get_text(" ", strip=True)) for li in soup.select(".alt-names-list li")
           if "more-alt-names" not in (li.get("class") or [])]
    if alt:
        meta.put("alt_titles", [a for a in alt if a and a not in _MORE])
    title = soup.select_one("h1[itemprop=name]")
    if title is not None:
        meta.put("title", title.get_text(" ", strip=True))
    text = soup.select_one("#content-desc-text") or soup.select_one(".content-desc [itemprop=description]")
    if text is not None:
        meta.put("description", block_text(text))
    ratings = []
    for a in soup.select(".content-ref-ids a"):
        site = _clean(a.get("data-balloon") or a.get("aria-label") or "")
        value = _clean(a.get_text(" ", strip=True))
        if site and value:
            ratings.append([site, value])
    if ratings:
        meta.data.setdefault("ratings", ratings)
    for img in soup.select(".poster-block img, .poster-wrapper img"):
        meta.poster(img.get("data-full") or img.get("data-src") or img.get("src"))


def _json_ld(soup, meta: _Meta) -> None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        for item in _ld_items(data):
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if not any(str(k) in ("Movie", "TVSeries", "TVSeason", "VideoObject", "CreativeWork", "CreativeWorkSeries",
                                  "Episode", "MediaObject") for k in kinds):
                continue
            meta.put("title", _ld_text(item.get("name")))
            alt = item.get("alternateName") or item.get("alternativeHeadline")
            if alt:
                meta.put("alt_titles", alt if isinstance(alt, list) else [alt])
            meta.ld_description = meta.ld_description or _ld_text(item.get("description"))
            genre = item.get("genre")
            if genre:
                meta.put("genres", genre if isinstance(genre, list) else _split(str(genre)))
            when = _ld_text(item.get("datePublished") or item.get("startDate") or item.get("dateCreated"))
            if when:
                meta.put("year", when[:4] if re.match(r"\d{4}", when) else when)
            for key, field_key in (("director", "director"), ("productionCompany", "studio")):
                names = [_ld_text(x.get("name") if isinstance(x, dict) else x) for x in _as_list(item.get(key))]
                if any(names):
                    meta.put(field_key, [n for n in names if n])
            if item.get("numberOfEpisodes"):
                meta.put("episodes", str(item["numberOfEpisodes"]))
            if item.get("contentRating"):
                meta.put("age", _ld_text(item["contentRating"]))
            for image in _as_list(item.get("image")):
                meta.poster(image.get("url") if isinstance(image, dict) else image)


def _ld_items(data):
    if isinstance(data, list):
        for x in data:
            yield from _ld_items(x)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _ld_items(data["@graph"])
        yield data


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ld_text(value) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or ""
    return _clean(value)


_ROW_SCOPES = ("main", "article", "[itemprop=mainEntity]", ".full-story", ".full", ".fstory", "#dle-content",
               ".content", "#content")
_BLOCKS = {"p", "div", "li", "h2", "h3", "h4", "h5", "h6", "section", "header"}
_NOT_VALUE = {"script", "style", "noscript", "iframe", "form", "select", "table", "nav", "aside", "footer", "h1", "h2",
              "h3", "h4", "h5", "h6", "button", "video", "audio", "img", "picture", "svg"}
_TEXT_TAGS = {"p", "span", "font", "em", "i", "b", "strong", "blockquote", "br", "small", "u"}
_LINE_NAME = re.compile(r"^\s*([^:：\n]{1,45})[:：]")


def _generic_rows(soup, meta: _Meta) -> None:
    """Lines «Поле: значение» whose name is known (other sites)."""
    scope = next((found for sel in _ROW_SCOPES if (found := soup.select_one(sel)) is not None), soup)
    for el in scope.find_all(["b", "strong", "span", "dt", "th", "label", "div", "td", "p", "h3", "h4", "h5"]):
        own = el.get_text(" ", strip=True)
        if not own or len(own) > 45:
            continue
        norm = _norm_label(own)
        key = key_of(norm)
        if key is None or (key in meta.data and key != "notes"):
            continue
        if not own.rstrip().endswith((":", "：")) and el.name not in ("dt", "th"):
            continue
        if el.name in ("dt", "th"):
            value_el = el.find_next_sibling(["dd", "td"])
            if value_el is None:
                continue
            meta.labeled(own, value_el.get_text(" ", strip=True), _items(value_el))
            meta.lines.append(el)
            continue
        label_el = el                   # the outermost element that holds only the line's name
        while label_el.parent is not None and _clean(label_el.parent.get_text(" ", strip=True)) == _clean(own):
            label_el = label_el.parent
        if label_el.name in _BLOCKS:
            # a line of its own with only the name: the value is what follows it («<p><b>Описание:</b></p><p>…</p>»)
            value = _value_after(label_el, key)
            if value:
                meta.labeled(own, value, [] if key == "description" else _items_after(label_el))
                meta.lines.append(label_el)
            continue
        container = label_el.parent
        if container is None or container is scope:
            continue
        whole = _clean(container.get_text(" ", strip=True))
        if not whole.startswith(_clean(own)):
            continue
        value = whole[len(_clean(own)):].strip()
        if not value or (len(value) > 500 and key != "description"):
            continue
        meta.labeled(own, value, _items(container, skip=el))
        meta.lines.append(container)


def _value_after(label_el, key: str) -> str:
    """The value of a line that has only its name: the element after it — for a description, the paragraphs after it,
    up to the next line «Поле: …» or anything that is not text."""
    from bs4.element import NavigableString, PreformattedString
    parts: list[str] = []
    for sib in label_el.next_siblings:
        name = getattr(sib, "name", None)
        if name is None:                                     # text between the elements (not a comment)
            if not isinstance(sib, NavigableString) or isinstance(sib, PreformattedString):
                continue
            text = _clean(str(sib))
        elif name in _NOT_VALUE or (parts and name not in _TEXT_TAGS):
            break
        else:
            text = block_text(sib) if key == "description" else sib.get_text(" ", strip=True)
        if not text:
            continue
        line = _LINE_NAME.match(text)
        line_key = key_of(_norm_label(line.group(1))) if line else None
        if line_key is not None and (parts or line_key != key):
            break                                            # the next line «Поле: …»
        parts.append(text if key == "description" else _clean(text))
        if key != "description" or sum(len(p) for p in parts) > MAX_TEXT:
            break
    value = "\n\n".join(parts)
    return value if key == "description" or len(value) <= 300 else ""


def _items_after(label_el) -> list[str]:
    nxt = label_el.find_next_sibling()
    return _items(nxt) if nxt is not None and nxt.name not in _NOT_VALUE else []


def _longer_text(soup, short: str) -> str:
    """The description a site cut for its meta tag or its structured data («…улетел в угаре в.», «Название — Эпизод 2.
    Текст… собирается с»): the page's own text that holds a piece from inside it and is at least as long as the rest
    — the innermost element that holds it ("" — the page has no such text)."""
    short = _clean(short)
    if len(short) < 40:
        return ""
    start = min(len(short) // 4, 80)                # past a title the site may put before the text
    end = short.rfind(" ", start, len(short))       # and before the word the site cut («…уплыл в.»)
    probe = short[start:min(start + 50, end if end > start + 20 else len(short))]
    best = None
    for el in soup.find_all(["p", "div", "span", "td", "section", "article", "blockquote"]):
        text = _clean(el.get_text(" ", strip=True))
        if len(text) >= len(short) - start and probe in text and (best is None or len(text) < best[0]):
            best = (len(text), el)
    return block_text(best[1]) if best is not None else ""


def _cyrillic(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text or "")
    return bool(letters) and sum(1 for c in letters if "а" <= c.lower() <= "я" or c.lower() == "ё") >= len(letters) / 2


def _russian_page(soup, tags: dict) -> bool:
    lang = str((soup.html.get("lang") if soup.html else "") or "").lower()
    return lang.startswith("ru") or str(tags.get("og:locale") or "").lower().startswith("ru")


def _native_paragraph(soup) -> str:
    """The longest Russian paragraph of the page (at least 120 characters), not in its menu or footer."""
    best = ""
    for p in soup.find_all("p"):
        if p.find_parent(["nav", "footer", "header", "aside", "form"]) is not None:
            continue
        text = block_text(p)
        if len(text) >= 120 and len(text) > len(best) and _cyrillic(text):
            best = text
    return best


def _page_description(soup, meta: _Meta, tags: dict) -> str:
    """When no line «Описание:» gave it: the description block of the page, else the page's own longer text of what
    its structured data or meta tag say (they are often cut), else what they say; a page in Russian whose meta tag is
    in another language (an advertisement of the site: «Watch … free») — its longest Russian paragraph."""
    text = _description(soup)
    if not text:
        short = meta.ld_description or tags.get("og:description") or tags.get("description") or ""
        text = _longer_text(soup, short) or short
    if not _cyrillic(text) and _russian_page(soup, tags):
        text = _native_paragraph(soup) or text
    return text


def _chip_rows(soup, meta: _Meta) -> None:
    """Lines whose name stands alone without a colon and whose values are links after it (hentaihaven:
    ``<div><span>Студия</span><a>Majin Petit</a></div>``)."""
    for span in soup.find_all("span"):
        own = _clean(span.get_text(" ", strip=True))
        key = _BY_LABEL.get(_norm_label(own)) if own and len(own) <= 30 and not own.endswith((":", "：")) else None
        if key is None or (key in meta.data and key != "notes") or span.find("a") is not None:
            continue
        row = span.parent
        children = [c for c in row.children if getattr(c, "name", None)] if row is not None else []
        if not children or children[0] is not span or len(children) < 2 or span.find_parent("a") is not None:
            continue
        links = [_clean(a.get_text(" ", strip=True)) for c in children[1:] for a in
                 ([c] if c.name == "a" else c.find_all("a"))]
        links = [x for x in links if x]
        if not links:
            continue
        meta.labeled(own, ", ".join(links), links)
        meta.lines.append(row)


_RATING = re.compile(r"(?:средний\s+)?(?:рейтинг|rating|оценка)\s*:?\s*(\d+(?:[.,]\d+)?)\s*(?:из|of|/)\s*(\d+)", re.I)


def _site_rating(soup, meta: _Meta, tags: dict, url: str) -> None:
    """The site's own rating of the title («Средний рейтинг 3.2 из 5» — an element's aria-label)."""
    if meta.data.get("ratings"):
        return
    for el in soup.find_all(attrs={"aria-label": True}):
        found = _RATING.search(str(el.get("aria-label") or ""))
        if found:
            site = _clean(tags.get("og:site_name") or "") or (urlparse(url).hostname or "").removeprefix("www.")
            meta.data["ratings"] = [[site or "Сайт", f"{found.group(1).replace(',', '.')} из {found.group(2)}"]]
            return


_ICONISH = {"icon", "icons", "logo", "avatar", "noavatar", "smile", "smiley", "emoji", "sprite", "button", "btn",
            "banner", "telegram", "loader", "spinner", "rating", "counter", "flag", "favicon"}


def _picture_src(img) -> str:
    return str(img.get("data-full") or img.get("data-src") or img.get("data-original") or img.get("src") or "").strip()


def _small_or_icon(img) -> bool:
    for attr in ("width", "height"):
        value = str(img.get(attr) or "").strip().rstrip("px")
        if value.isdigit() and int(value) < 100:
            return True
    name = PurePosixPath(urlparse(_picture_src(img)).path).name.lower()
    words = set(re.split(r"[-_.\s]+", name)) | {c.lower() for c in img.get("class") or []}
    words |= set(re.split(r"[-_\s]+", str(img.get("id") or "").lower()))
    return bool(words & _ICONISH)


def _is_picture_link(href: str) -> bool:
    return bool(re.search(r"\.(jpe?g|png|webp|avif|gif)(?:$|\?)", str(href or ""), re.I))


def _pictures_beside(lines: list) -> list[str]:
    """The pictures next to the lines of the description (a poster beside «Год: …, Жанр: …»): the first ones found
    going up to four levels from the first line — not inside a link to another page, not an icon."""
    if not lines:
        return []
    node = lines[0]
    for _level in range(4):
        node = node.parent
        if node is None or node.name in ("body", "html", "[document]"):
            break
        found: list[str] = []
        for img in node.find_all("img"):
            src = _picture_src(img)
            if not src or src.startswith("data:") or _small_or_icon(img):
                continue
            link = img.find_parent("a")
            if link is not None and link.get("href"):
                if not _is_picture_link(link["href"]):
                    continue                                   # a link to another title («Смотрите также»)
                found.append(str(link["href"]))                # the picture in its full size
            found.append(src)
        if found:
            return found[:3]
    return []


def _pictures_named(soup, title: str) -> list[str]:
    """Pictures whose ``alt`` or ``title`` is the series' title (``<img alt="Название">``)."""
    head = _clean(title).lower()[:30]
    if len(head) < 4:
        return []
    out = []
    for img in soup.find_all("img"):
        text = _clean(img.get("alt") or img.get("title") or "").lower()
        if text.startswith(head) and not _small_or_icon(img) and img.find_parent("a") is None:
            src = _picture_src(img)
            if src and not src.startswith("data:"):
                out.append(src)
    return out[:2]


def _title(soup, meta: _Meta, tags: dict) -> None:
    h1 = next((found for sel in ("header.short-head h1", ".short-head h1", "h1[itemprop=name]", "article h1", "h1")
               if (found := soup.select_one(sel)) is not None), None)
    text = _clean(h1.get_text(" ", strip=True)) if h1 is not None else ""
    if not text:
        text = _clean(tags.get("og:title") or (soup.title.get_text(" ", strip=True) if soup.title else ""))
        text = re.split(r"\s+[—|–]\s+|\s+-\s+(?=[^-]*$)", text)[0] if text else ""
    if text:
        meta.put("title", text)
    _split_title(meta)


def _page_number(url: str) -> str:
    """The title's number in its page's address: «anime.php?id=760», «/1234-name.html», «/anime/760/»; "" — none."""
    parsed = urlparse(str(url or ""))
    for key, value in parse_qsl(parsed.query):
        if key.lower() in ("id", "aid", "anime_id", "anime", "news_id", "newsid") and value.isdigit():
            return value
    parts = [p for p in parsed.path.split("/") if p]
    found = re.match(r"^(\d{2,})(?:[-_.]|$)", parts[-1]) if parts else None
    return found.group(1) if found else ""


def _pictures_by_number(soup, url: str) -> list[str]:
    """Pictures named by the title's number (``?id=760`` → ``…/760.webp``) — not inside a link to another title."""
    number = _page_number(url)
    if not number:
        return []
    out = []
    for img in soup.find_all("img"):
        src = _picture_src(img)
        stem = PurePosixPath(unquote(urlparse(src).path)).stem
        if not src or src.startswith("data:") or not re.match(rf"^{number}(?:$|[-_])", stem) or _small_or_icon(img):
            continue
        link = img.find_parent("a")
        if link is not None and link.get("href") and not _is_picture_link(link["href"]):
            continue
        out.append(src)
    return out[:2]


def _alt_titles(soup, meta: _Meta) -> None:
    """Other names of the title: a line «Русское|Romaji» that starts with the title, and ``<h2>`` that comes with
    ``<h1>`` alone in their block (``<div><h1>Русское</h1><h2>Romaji</h2></div>``)."""
    title = str(meta.data.get("title") or "")
    if not title:
        return
    found = []
    for el in soup.find_all(["p", "div", "span", "h2", "h3", "td"]):
        text = _clean(el.get_text(" ", strip=True))
        if len(text) > 400 or not text.startswith(title):
            continue
        rest = text[len(title):].lstrip()
        if rest[:1] == "|":
            found += [p.strip() for p in rest[1:].split("|") if p.strip()]
            break
    h1 = soup.find("h1")
    parent = h1.parent if h1 is not None else None
    if parent is not None:
        blocks = [child for child in parent.children if getattr(child, "name", None)]
        if len(blocks) == 2 and blocks[0] is h1 and blocks[1].name == "h2":
            text = _clean(blocks[1].get_text(" ", strip=True))
            if 3 <= len(text) <= 250:
                found.append(text)
    found = [t for t in found if t.lower() != title.lower()]
    if found:
        meta.data["alt_titles"] = list(dict.fromkeys(list(meta.data.get("alt_titles") or []) + found))


_EPISODE_END = re.compile(r"\s*[—–:|-]?\s*(?:эпизод|серия|episode|ep\.?)\s*\d+\s*$", re.I)


def _split_title(meta: _Meta) -> None:
    """«Русское / Romaji (2026г.)»: the first name is the title, the others go to the other titles, the year to its
    line."""
    text = str(meta.data.get("title") or "")
    if not text:
        return
    series = _EPISODE_END.sub("", text).strip()
    if series and series != text:
        text = series                         # the page of an episode: the title of the series
    year = re.search(r"\(\s*((?:19|20)\d{2})\s*(?:г\.?|\.г)?\s*\)\s*$", text)
    if year:
        meta.put("year", year.group(1))
        text = text[:year.start()].strip()
    parts = [p.strip() for p in re.split(r"\s+/\s+", text) if p.strip()]
    meta.data["title"] = parts[0] if parts else text
    if len(parts) > 1:
        alt = list(meta.data.get("alt_titles") or [])
        meta.data["alt_titles"] = list(dict.fromkeys(alt + parts[1:]))


def _prose(text: str) -> int:
    """Characters of a block's lines that are sentences (≥ 60 characters), not facts («Год выпуска:» / «2026»)."""
    return sum(len(line) for line in text.split("\n") if len(line.strip()) >= 60)


def _description(soup) -> str:
    """The title's description: the block with the most sentences (hfshare: «.full-text» is its facts, the story is
    «.full-story-content»), of those the longest."""
    best, key = "", (-1, -1)
    for el in soup.select("[itemprop=description], .full-text, .fdesc, .description, .story, .film-desc, "
                          ".full-story-content"):
        text = block_text(el)
        if (_prose(text), len(text)) > key:
            best, key = text, (_prose(text), len(text))
    return best if len(best) >= 40 else ""


# ── the player's files and who voiced them ────────────────────────────────────────────
_FILE_RE = re.compile(r"""\{\s*["']?file["']?\s*:\s*["']([^"']+)["']\s*,\s*["']?title["']?\s*:\s*["']([^"']*)["']""")
_FILE_RE_TITLE_FIRST = re.compile(
    r"""\{\s*["']?title["']?\s*:\s*["']([^"']*)["']\s*,\s*["']?file["']?\s*:\s*["']([^"']+)["']""")


def _player_files(html: str) -> list[dict]:
    """``new Playerjs({file:[{file:"…", title:"…"}, …]})``: the files the site plays, by the name of each file."""
    out = []
    found = [(m.start(), m.group(1), m.group(2)) for m in _FILE_RE.finditer(html or "")]
    found += [(m.start(), m.group(2), m.group(1)) for m in _FILE_RE_TITLE_FIRST.finditer(html or "")]
    for _at, address, title in sorted(found):
        name = unquote(urlparse(address).path.rsplit("/", 1)[-1])
        if not name or not re.search(r"\.(mp4|mkv|webm|m4v|mov|avi|flv|ts)$", name, re.I):
            continue
        if not any(f["name"] == name for f in out):
            out.append({"name": name, "title": _clean(title)})
    return out


_FILES_SPAN = r"(?:с\s*)?\d+(?:\s*(?:[-–—]|по|до)\s*\d+)?"      # «3», «1-6», «с 7 по 12»
_FILES_WHICH = re.compile(rf"\s*[Фф]айл[ыа]?\s*№?\s*({_FILES_SPAN}(?:\s*(?:,|и|&)\s*{_FILES_SPAN})*)\s*[-–—:]\s*(.+)$")


def _file_notes(files: list[dict], notes: list[str]) -> None:
    """«Файл 1 - озвучка от AniStar, файлы 2,3 - озвучка от FumoDub, файл 4,5 - субтитры…», «Файлы 1-6 - озвучка
    Anistar, файлы 7-12 - субтитры от …» (hentasis, 04.11.2026: «1-6» was the file 1 of «6 - озвучка Anistar»): each
    file's part."""
    numbered = {}
    for note in notes:
        for part in re.split(r"[,;]\s*(?=[Фф]айл)", note):
            match = _FILES_WHICH.match(part)
            if not match:
                continue
            text = match.group(2).strip(" .!")
            for first, last in re.findall(r"(\d+)(?:\s*(?:[-–—]|по|до)\s*(\d+))?", match.group(1)):
                first = int(first)
                last = int(last) if last and first <= int(last) <= first + 500 else first
                for number in range(first, last + 1):
                    numbered.setdefault(number, text)
    for item in files:
        number = re.search(r"(\d+)\s*$", item.get("title") or "")
        if number and int(number.group(1)) in numbered:
            item["note"] = numbered[int(number.group(1))]
