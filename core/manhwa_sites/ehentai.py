"""Public e-hentai galleries: ordinary HTML pages and the documented metadata API, never ExHentai.

Metadata contract: https://ehwiki.org/wiki/API (gdata, at most 25 entries per request).
HTML contract checked against the source of the public reader extension:
https://github.com/yuzono/cursed-manga-extensions/tree/main/src/all/ehentai.
Only #gdt thumbnail links and #img of their ordinary viewer pages are read. A refusal, sign-in request,
viewing limit or missing page is returned as such; no cookies, archive API, alternate host or challenge flow.
Hash searches read one ordinary public result page and at most six metadata-confirmed galleries. Searches share
a 3-second interval (https://ehwiki.org/wiki/Gallery_Searching); other requests retain the 1.1-second interval.
An expunged visibility flag needs a readable public gallery page, not login.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from urllib.parse import parse_qs, parse_qsl, quote, urljoin, urlparse

from core import gates as G, manhwa_sources as S

_PACE = 1.1
_SEARCH_PACE = 3.0
_LOCK = threading.Lock()
_NEXT: dict = {}
_SEARCH_NEXT: dict = {}
_GALLERY = re.compile(r"/g/(\d{1,10})/([a-f0-9]{10})/?$", re.I)
_VIEWER = re.compile(r"/s/([a-f0-9]{10})/(\d{1,10})-(\d{1,6})/?$", re.I)
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
COMIC_CATEGORIES = frozenset(("manga", "doujinshi", "western"))
PHOTO_CATEGORIES = frozenset(("misc", "asian porn", "image set", "cosplay", "artist cg", "game cg", "non-h"))


class _ViewingLimit(S.Refused):
    """A site-wide viewing limit stops this search, rather than rejecting just one gallery."""


class SearchResults(list):
    """Available hits; incomplete means another candidate failed temporarily and this list must not be cached."""

    def __init__(self, hits, *, incomplete: bool = False):
        super().__init__(hits)
        self.incomplete = incomplete


def _hash_query(url: str) -> dict:
    text = str(url or "").strip()
    if len(text) > 2048 or any(ord(char) <= 32 for char in text):
        return {}
    try:
        parsed = urlparse(text)
        if parsed.scheme not in ("https", "http") or parsed.hostname not in ("e-hentai.org", "www.e-hentai.org") \
                or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.params \
                or parsed.path not in ("", "/") or parsed.port not in (None, 443 if parsed.scheme == "https" else 80):
            return {}
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
    except ValueError:
        return {}
    query = dict(pairs)
    if len(query) != len(pairs) or set(query) - {"f_shash", "fs_exp", "fs_similar", "f_sh"} or \
            not re.fullmatch(r"[a-fA-F0-9]{40}", query.get("f_shash", "")) or \
            "f_sh" in query and query["f_sh"] != "on" or \
            any(query[key] not in ("0", "1") for key in ("fs_exp", "fs_similar") if key in query):
        return {}
    query["f_shash"] = query["f_shash"].lower()
    return query


def hash_of(url: str) -> str:
    """A SHA-1 from an ordinary public hash-search URL, or empty; never an arbitrary URL to fetch."""
    return _hash_query(url).get("f_shash", "")


def hash_link(url: str) -> str:
    """Canonical public hash search, retaining only validated public search flags supplied in the link."""
    query = _hash_query(url)
    return ("https://e-hentai.org/?" + "&".join(key + "=" + query[key]
            for key in ("f_shash", "fs_exp", "fs_similar", "f_sh") if key in query)) if query else ""


def is_photo(title: S.TitleRef) -> bool:
    """Source metadata for the image-set route, not an image-content detector; ordinary Western stays a comic."""
    return title.site == "ehentai" and (str(title.type or "").strip().casefold() in PHOTO_CATEGORIES or any(
        str(tag).strip().casefold().rsplit(":", 1)[-1] == "realporn" for tag in title.tags or ()))


def _attr(tag: str, key: str) -> str:
    match = re.search(r"\b" + re.escape(key) + r"\s*=\s*([\"'])(.*?)\1", tag, re.I | re.S)
    return html.unescape(match.group(2)).strip() if match else ""


class EHentai(S.Source):
    name = "ehentai"
    label = "E-Hentai"
    hosts = ("e-hentai.org",)
    address_value = "EHENTAI_SITE"
    api_value = "EHENTAI_API"
    cover_hosts = ("ehgt.org", "e-hentai.org")
    adult_only = True
    SEARCH_HITS = 12
    HASH_HITS = 6
    LIST_PAGES = 50
    MAX_PICTURES = 2000
    HTML_KEPT = 6

    def site(self) -> str:
        return S.address(self.name) or S.EHENTAI_SITE

    def api(self) -> str:
        return S.address(self.name + "_api") or S.EHENTAI_API

    def key_of(self, url: str) -> str:
        if not S._host_of(url, self.hosts, self.site()):
            return ""
        match = _GALLERY.fullmatch(urlparse(url).path)
        return f"{int(match.group(1))}/{match.group(2).lower()}" if match else ""

    def _url(self, key: str) -> str:
        if not re.fullmatch(r"\d{1,10}/[a-f0-9]{10}", str(key), re.I):
            raise S.Missing("E-Hentai: нужна полная ссылка на галерею")
        return f"{self.site()}/g/{key}/"

    def _public(self, net, url: str, *, search: bool = False, **options) -> tuple:
        """Requests stay on the ordinary HTTP path, including when the supplied Net can open browsers."""
        stop = getattr(net, "stop_check", None)
        if callable(stop) and stop():
            raise S.SourceError("остановлено")
        place = urlparse(url).netloc
        while True:
            if callable(stop) and stop():
                raise S.SourceError("остановлено")
            with _LOCK:
                now = time.monotonic()
                at = max(_NEXT.get(place, 0.0), _SEARCH_NEXT.get(place, 0.0) if search else 0.0)
                if now >= at:
                    _NEXT[place] = now + _PACE
                    if search:
                        _SEARCH_NEXT[place] = now + _SEARCH_PACE
                    break
            time.sleep(min(0.1, max(0.0, at - time.monotonic())))
        if callable(stop) and stop():
            raise S.SourceError("остановлено")
        fetch = net._fetch if isinstance(net, S.Net) else net.fetch
        result = fetch(url, what=self.label, tries=1, **options)
        if callable(stop) and stop():
            raise S.SourceError("остановлено")
        return result

    def _page(self, net, url: str, *, search: bool = False) -> str:
        body, ctype = self._public(net, url, search=search, headers={"Accept": "text/html"})
        text = S._decoded(body, ctype)
        if S.still_check(text, url):
            raise S.Closed("E-Hentai ожидает проверку доступа")
        low = S._text(text).casefold()
        if any(word in low for word in ("exceeded your image viewing limits", "temporarily banned", "ip address has been")):
            raise _ViewingLimit("E-Hentai ограничил просмотр; повторять сейчас нельзя")
        if any(word in low for word in ("gallery has been removed", "gallery not found", "gallery is unavailable")):
            raise S.Missing("E-Hentai: галерея недоступна")
        if any(word in low for word in ("sad panda", "requires you to log", "must be logged in", "offensive for everyone")):
            raise S.Refused("E-Hentai не отдаёт эту страницу без дополнительного доступа")
        return text

    def _metadata(self, net, keys: list) -> list:
        pairs = [key.split("/", 1) for key in keys[:25] if self.key_of(self._url(key))]
        body, _ctype = self._public(net, self.api(), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            data=json.dumps({"method": "gdata", "gidlist": [[int(gid), token] for gid, token in pairs], "namespace": 1}))
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise S.Silent("E-Hentai прислал непонятное описание галереи") from exc
        rows = data.get("gmetadata") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise S.Silent("E-Hentai не прислал описание галереи")
        allowed = set(keys)
        found, denied = {}, set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            gid = S._int(row.get("gid"))
            key = f"{gid}/{str(row.get('token') or '').lower()}"
            if row.get("error"):
                denied.add(str(gid))
            elif key in allowed:
                found.setdefault(key, row)
        if any(key not in found and key.split("/", 1)[0] not in denied for key in keys):
            raise S.Silent("E-Hentai прислал неполное описание галерей")
        return [found[key] for key in keys if key in found]

    def _title(self, row: dict) -> S.TitleRef:
        from core.nhentai import pretty
        key = f"{S._int(row.get('gid'))}/{str(row.get('token') or '').lower()}"
        whole = [html.unescape(str(row.get(field) or "")).strip() for field in ("title", "title_jpn")]
        names = list(dict.fromkeys(n for value in whole if value for n in (pretty(value) or value, value)))
        if not names or row.get("error") or str(row.get("category") or "").strip().casefold() == "private":
            raise S.Missing("E-Hentai: галерея недоступна")
        tags = [str(tag) for tag in row.get("tags") or [] if isinstance(tag, str)]
        words = list(dict.fromkeys([*tags, *(tag.split(":", 1)[-1] for tag in tags)]))
        category = str(row.get("category") or "Gallery")
        title = S.TitleRef(site=self.name, key=key, url=self._url(key), title=names[0], alt_titles=names[1:],
            type=category, age="18+", is_erotic=True, genres=[category], tags=words,
            cover_url=str(row.get("thumb") or ""), authors=[tag.split(":", 1)[1] for tag in tags if tag.startswith("artist:")],
            branches=[S.Branch(id="", name=self.label)])
        return S.checked(title)

    def _public_title(self, net, row: dict, title: S.TitleRef) -> None:
        """Expunged describes listing visibility; require actual ordinary public viewer links before accepting it."""
        if str(row.get("expunged") or "").strip().casefold() in ("", "0", "false"):
            return
        text = self._page(net, title.url)
        gid = title.key.split("/", 1)[0]
        for attrs, _inner in _ANCHOR.findall(S._by_id(text, "gdt")):
            url = urljoin(self.site(), _attr(attrs, "href"))
            match = _VIEWER.fullmatch(urlparse(url).path)
            if match and match.group(2) == gid and S._host_of(url, self.hosts, self.site()):
                # Reuse this already checked index once, in this Net only; never persist gallery HTML.
                net.kept[(self.name, title.key, "public_html")] = text
                saved = [key for key in net.kept if isinstance(key, tuple) and len(key) == 3
                         and key[0] == self.name and key[2] == "public_html"]
                for old in saved[:-self.HTML_KEPT]:
                    net.kept.pop(old, None)
                return
        raise S.Silent("E-Hentai не прислал открытые страницы скрытой из поиска галереи")

    def title(self, net, key: str) -> S.TitleRef:
        self._url(key)
        kept = net.kept.get((self.name, key, "title"))
        if kept is None:
            rows = self._metadata(net, [key])
            if not rows:
                raise S.Missing("E-Hentai: такой открытой галереи нет")
            kept = self._title(rows[0])
            self._public_title(net, rows[0], kept)
            net.kept[(self.name, key, "title")] = kept
            net.kept[(self.name, key, "count")] = S._int(rows[0].get("filecount"))
        return S.checked(kept)

    def chapters(self, net, title: S.TitleRef, branch: str = "") -> list:
        S.checked(title)
        return [S.ChapterRef(id=title.key, number="1", name=title.title, url=title.url, index=1)]

    def search(self, net, query: str) -> list:
        if not query.strip() or G.title(self.name, names=[query], adult=True):
            return []
        text = self._page(net, self.site() + "/?f_search=" + quote(query[:180]), search=True)
        keys = []
        for attrs, inner in _ANCHOR.findall(text):
            key = self.key_of(urljoin(self.site(), _attr(attrs, "href")))
            name = S._text(inner)
            if key and key not in keys and name and not G.title(self.name, names=[name], adult=True):
                keys.append(key)
            if len(keys) >= self.SEARCH_HITS:
                break
        if not keys:
            return []
        return self._hits(net, keys, comics_only=True)

    def search_hash(self, net, url: str) -> list:
        """One public SHA-1 search page, at most HASH_HITS real galleries; names and pixels still need confirmation."""
        canonical = hash_link(url)
        if not canonical:
            raise S.Missing("E-Hentai: нужна публичная ссылка поиска с SHA-1 картинки")
        text = self._page(net, self.site().rstrip("/") + "/?" + urlparse(canonical).query, search=True)
        keys = []
        for attrs, _inner in _ANCHOR.findall(text):
            key = self.key_of(urljoin(self.site(), _attr(attrs, "href")))
            if key and key not in keys:
                keys.append(key)
            if len(keys) >= self.HASH_HITS:
                break
        if not keys:
            if any(words in S._text(text).casefold() for words in ("no hits found", "no results found", "no galleries found")):
                return []
            raise S.Silent("E-Hentai не прислал результат поиска картинки")
        return self._hits(net, keys, comics_only=False)

    def _hits(self, net, keys: list, *, comics_only: bool) -> list:
        out, unknown = [], None
        for row in self._metadata(net, keys):
            stop = getattr(net, "stop_check", None)
            if callable(stop) and stop():
                raise S.SourceError("остановлено")
            try:
                title = self._title(row)
                if comics_only and (title.type.casefold() not in COMIC_CATEGORIES or is_photo(title)):
                    continue                            # a name search does not classify image sets as comics
                self._public_title(net, row, title)
            except _ViewingLimit:
                raise
            except (S.Excluded, S.Missing, S.Refused):
                continue
            except S.SourceError as exc:
                if not S.transient(exc):
                    raise
                unknown = unknown or exc
                continue
            net.kept[(self.name, title.key, "title")] = title
            net.kept[(self.name, title.key, "count")] = S._int(row.get("filecount"))
            out.append(S.Hit(site=self.name, key=title.key, names=[title.title, *title.alt_titles], type=title.type,
                             url=title.url, cover=title.cover_url, chapters=1, adult=True))
        if not out and unknown is not None:
            raise unknown
        return SearchResults(S.checked_hits(out), incomplete=unknown is not None)

    def pictures(self, net, title: S.TitleRef, chapter: S.ChapterRef) -> list:
        S.checked(title)
        if not chapter.free:
            raise S.Paid("Платные главы не скачиваются")
        if chapter.id != title.key:
            raise S.Missing("E-Hentai: страница другой галереи")
        cache_key = (self.name, title.key, "pictures")
        cached = net.kept.get(cache_key)
        if cached is not None:
            return list(cached)
        count = int(net.kept.get((self.name, title.key, "count")) or 0)
        if count < 1:
            raise S.Silent("E-Hentai не прислал число страниц галереи")
        if count > self.MAX_PICTURES:
            raise S.Refused("E-Hentai: превышен предел числа страниц галереи")
        wanted_gid = title.key.split("/", 1)[0]
        base = self._url(title.key)
        links, pending, visited = {}, [base], set()
        while pending and len(visited) < self.LIST_PAGES:
            address = pending.pop(0)
            if address in visited:
                continue
            visited.add(address)
            text = net.kept.pop((self.name, title.key, "public_html"), None) if address == base else None
            if text is None:
                text = self._page(net, address)
            for attrs, _inner in _ANCHOR.findall(S._by_id(text, "gdt")):
                url = urljoin(self.site(), _attr(attrs, "href"))
                match = _VIEWER.fullmatch(urlparse(url).path)
                if match and match.group(2) == wanted_gid and S._host_of(url, self.hosts, self.site()):
                    n = int(match.group(3))
                    if n <= self.MAX_PICTURES:
                        links.setdefault(n, url)
            for attrs, _inner in _ANCHOR.findall(text):
                url = urljoin(self.site(), _attr(attrs, "href"))
                if self.key_of(url) != title.key:
                    continue
                try:
                    pages = parse_qs(urlparse(url).query, keep_blank_values=True, max_num_fields=16).get("p") or []
                except ValueError:
                    continue
                if len(pages) != 1 or not re.fullmatch(r"[0-9]{1,3}", pages[0]):
                    continue
                page = int(pages[0])
                canonical = base + f"?p={page}" if page else base
                if page < self.LIST_PAGES and canonical not in visited and canonical not in pending:
                    pending.append(canonical)
        if not links:
            raise S.Refused("E-Hentai не отдал обычные страницы галереи")
        if set(links) != set(range(1, count + 1)) or pending:
            raise S.Silent("E-Hentai прислал не все страницы галереи")
        pictures = [S.Picture(url=links[n], source=self.name) for n in sorted(links)]
        net.kept[cache_key] = pictures
        return list(pictures)

    def resolve_picture(self, net, picture: S.Picture) -> S.Picture:
        if not S._host_of(picture.url, self.hosts, self.site()) or not _VIEWER.fullmatch(urlparse(picture.url).path):
            raise S.Missing("E-Hentai: неправильная ссылка на страницу")
        page = self._page(net, picture.url)
        image = next((tag for tag in re.findall(r"<img\b[^>]*>", page, re.I) if _attr(tag, "id") == "img"), "")
        url = urljoin(picture.url, _attr(image, "src")) if image else ""
        if not url or urlparse(url).scheme not in ("https", "http"):
            raise S.Refused("E-Hentai не отдал картинку страницы")
        if any(part in urlparse(url).path.casefold() for part in ("509.gif", "509.png", "sadpanda")):
            raise S.Refused("E-Hentai ограничил просмотр картинок")
        return S.Picture(url=url, width=S._int(_attr(image, "width")), height=S._int(_attr(image, "height")),
                         direct_only=True)
