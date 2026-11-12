"""(v5.44.97) nhentai's API v2 — what the program asks of it: a gallery (the download, ``core/manga_download.py``; the
card of «Скачать → По ссылке»), its lists and its search («Манга → Лента», ``core/catalog.py``), the names of tags by
their numbers.

The owner (6.11.2026): «дак а блять с nhentai показывать мангу?».  Asked from his computer on 6.11.2026
(``_to_delete/cl96_research23–26.txt``): the old API (``/api/gallery/<n>``, ``/api/galleries/…``) answers 403 «Use new
API https://nhentai.net/api/v2/docs» — the downloader of 5.23 got no description from it any more; the new one
(FastAPI: its description — ``/api/v2/openapi.json``) answers without a sign-in and without a check for robots:

* ``/api/v2/galleries/<n>`` — a gallery: ``id``, ``media_id``, ``title`` {english, japanese, pretty}, ``tags``
  [{id, type, name, slug, url, count}], ``num_pages``, ``pages`` [{number, path «galleries/<media>/<n>.<ext>», width,
  height, thumbnail «galleries/<media>/<n>t.<ext>» (sometimes «.webp.webp»), …}], ``cover``/``thumbnail`` {path, width,
  height}, ``upload_date`` (seconds), ``num_favorites``; one that is not there — 404 {"error": "Gallery not found"};
* ``/api/v2/cdn`` — its servers: ``image_servers`` (i1…i4.nhentai.net), ``thumb_servers`` (t1…t4.nhentai.net);
* ``/api/v2/galleries?page=&per_page=`` — the newest (25 a page): ``result`` [{id, media_id, english_title,
  japanese_title, thumbnail, num_pages, num_favorites, tag_ids, …}], ``num_pages``, ``total``;
* ``/api/v2/search?query=&sort=date|popular|popular-today|popular-week|popular-month&page=`` — 25 a page; ``-tag:x``
  leaves a tag out (``*`` — 659 264 galleries); ten searches a minute (429,
  ``Retry-After: 60``) — the newest are taken from the list, not the search;
* ``/api/v2/tags/ids?ids=1,2,3`` — tags by their numbers; ``/api/v2/tags/<type>/<slug>`` — one by its name;
* (v5.44.148, asked 11.11.2026) ``POST /api/v2/tags/search`` {type or null, query, limit} — its tags whose names begin
  with the words: [{id, type, name, slug, count, …}] (tag, artist, character, parody, group, language, category);
  ``/api/v2/tags/<type>?sort=popular|name&page=&per_page=`` — every tag of a kind, 120 a page (4 775 tags, 33 350
  artists, 18 006 characters, 4 401 parodies, 25 383 groups); the search takes ``tag:"…"``, ``artist:"…"``… and the
  same with «-».

It has no Russian: its languages are Japanese, English, Chinese («language/russian» — 404).

The program's gates (``core/gates.py``): the tags ``gates.left_out`` names are left out of every search
(:func:`search_words`); a gallery ``gates.title`` closes — by what :func:`gallery_facts` reads of it — is not a tile, is
not described, not downloaded."""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from urllib.parse import urlencode, urlparse

from core import gates as G
from core import manhwa_sources as S

SITE = "https://nhentai.net"
API = SITE + "/api/v2"                     # (the checks put their own)
IMAGE_SERVERS = ("https://i1.nhentai.net", "https://i2.nhentai.net", "https://i3.nhentai.net",
                 "https://i4.nhentai.net")
THUMB_SERVERS = ("https://t1.nhentai.net", "https://t2.nhentai.net", "https://t3.nhentai.net",
                 "https://t4.nhentai.net")
PAGE_SIZE = 25
TAGS_KEPT = 40000                          # names of tags kept (they do not change)
TAGS_AT_ONCE = 100                         # numbers asked in one request

_TAGS: "OrderedDict[int, tuple]" = OrderedDict()       # a tag's number → (its type, its name)
_LOCK = threading.Lock()
_BRACKETS = re.compile(r"\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|【[^】]*】|（[^）]*）)\s*")


def search_words(text: str = "", also: str = "") -> str:
    """The words of a search: the person's, (v5.44.148) the filters' (``also`` — «tag:"…" -artist:"…"», made by
    ``catalog.nhentai_words``) and the tags its lists are always asked without (``gates.left_out``), each «-tag:"…"»."""
    words = " ".join(str(text or "").split())[:200]
    return " ".join(x for x in (words, str(also or "").strip(),
                                *(f'-tag:"{name}"' for name in G.left_out("nhentai").names)) if x)


def list_url(how: str, page: int, text: str = "", also: str = "") -> str:
    """The address of a page of a list: ``how`` — «date» (the newest: its list, not the search — the search is ten a
    minute) or an order of its search («popular», «popular-week»…); ``text`` — words searched, ``also`` — the
    filters' words (:func:`search_words`)."""
    page = max(1, int(page or 1))
    if text or also or how != "date":
        return f"{API}/search?" + urlencode({"query": search_words(text, also), "sort": how or "date", "page": page})
    return f"{API}/galleries?" + urlencode({"page": page, "per_page": PAGE_SIZE})


def gallery_api(number: int) -> str:
    return f"{API}/galleries/{int(number)}"


def link_of(number) -> str:
    return f"{SITE}/g/{int(number)}/"


def path_ok(path, media: str = "") -> bool:
    """A picture's path of the API: «galleries/<media>/<name>» (the media — the gallery's own, when given)."""
    m = re.fullmatch(r"galleries/(\d{1,12})/[A-Za-z0-9_.-]{1,40}", str(path or "").lstrip("/"))
    return bool(m) and (not media or m.group(1) == str(media))


def thumb_of(path, number: int = 0) -> str:
    """A thumbnail's address on its servers (by the gallery's number — the same server for the same gallery), ""
    — a path that is not the API's."""
    if not path_ok(path):
        return ""
    server = THUMB_SERVERS[int(number or 0) % len(THUMB_SERVERS)]
    return f"{server.rstrip('/')}/{str(path).lstrip('/')}"


def servers_ok(servers, own: tuple, also=()) -> list:
    """The servers the API named (``/api/v2/cdn``), only addresses of the site (or of the check's site — the host of
    :data:`API`, the hosts ``also``: the host of the API the description came from); none — ``own``."""
    hosts = {(urlparse(API).hostname or "").lower(), *(str(h or "").lower() for h in also if h)}
    out = []
    for server in servers if isinstance(servers, list) else []:
        parsed = urlparse(str(server or ""))
        host = (parsed.hostname or "").lower()
        if parsed.scheme in ("http", "https") and (host == "nhentai.net" or host.endswith(".nhentai.net")
                                                   or host in hosts) and str(server) not in out:
            out.append(str(server).rstrip("/"))
    return out or list(own)


def pretty(title: str) -> str:
    """A title without its brackets — the artist, the event, the language, the translators: «[Artist] Name (Book)
    [English] [Group]» → «Name» ("" — nothing left)."""
    text = str(title or "")
    for _ in range(3):                          # nested and repeated brackets
        text = _BRACKETS.sub(" ", text)
    return " ".join(text.split()).strip(" -|~:")


def known_tags(ids) -> dict:
    """{number: name} of the tags whose names the program has already been told."""
    with _LOCK:
        return {i: _TAGS[i][1] for i in ids if i in _TAGS}


def tag_names(net, ids, *, what: str = "nhentai") -> dict:
    """{number: name} of tags — asked of the site by their numbers (``/tags/ids``, :data:`TAGS_AT_ONCE` at once) the
    first time, kept after; a request that did not come — those stay unknown (the gallery is judged by the rest)."""
    wanted = [int(i) for i in dict.fromkeys(ids or ()) if isinstance(i, int) or str(i).isdigit()]
    with _LOCK:
        unknown = [i for i in wanted if i not in _TAGS]
    for n in range(0, len(unknown), TAGS_AT_ONCE):
        chunk = unknown[n:n + TAGS_AT_ONCE]
        try:
            data = net.json(f"{API}/tags/ids?ids=" + ",".join(map(str, chunk)), what=what, tries=1,
                            headers={"Accept": "application/json", "Referer": SITE + "/"})
        except S.SourceError:
            break
        with _LOCK:
            for tag in data if isinstance(data, list) else []:
                if isinstance(tag, dict) and S._int(tag.get("id")):
                    _TAGS[S._int(tag.get("id"))] = (str(tag.get("type") or ""), str(tag.get("name") or ""))
            while len(_TAGS) > TAGS_KEPT:
                _TAGS.popitem(last=False)
    return known_tags(wanted)


def forget() -> None:
    with _LOCK:
        _TAGS.clear()


def gallery_facts(data: dict) -> dict:
    """What a gallery's description (the API's, of both of its versions) says of it, for the program's gate —
    ``gates.title("nhentai", **gallery_facts(data))``: its titles, its tags' names and numbers."""
    title = data.get("title") if isinstance(data.get("title"), dict) else {}
    tags = [t for t in data.get("tags") or [] if isinstance(t, dict)]
    return {"names": [str(title.get(k) or "") for k in ("english", "japanese", "pretty")],
            "tags": [str(t.get("name") or "") for t in tags], "tag_ids": [t.get("id") for t in tags]}
