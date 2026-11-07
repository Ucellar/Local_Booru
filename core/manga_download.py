"""Manga from nhentai by its link or number (the tab «Манга» → «Скачать с nhentai», ``/manga/get``).

* :func:`gallery_ids` — what the person pasted: links ``https://nhentai.net/g/691957/`` (a page of it too) and bare
  numbers (``691957``, ``#691957``), several at once;
* :func:`description` — the gallery's description from the site's API (its pages and their kinds, titles, tags);
  a check for robots (Cloudflare) → the program window's browser opens the API address, then the gallery's page
  (its ``window._gallery`` carries the same description — (v5.44.97) the page of 2026 has it among the answers its
  scripts were given, ``<script data-sveltekit-fetched>``; failing that its page thumbnails and tags are read);
  (v5.44.97) the API of 2026 (``core/nhentai.py``): ``/api/v2/galleries/<n>`` — the old ``/api/gallery/<n>`` answers
  403 «Use new API»; the pages by their paths on the servers ``/api/v2/cdn`` names; the program's rule about minors —
  such a gallery is neither described nor downloaded (:class:`Forbidden`);
* :func:`download` — the galleries one after another into the manga folder, each as ``nhentai-<number> - <title>``
  with pages ``001.jpg`` … and the description as ``meta.json`` (``core/manga_library.py`` reads it: the titles, the
  tags by kind, the link back).  A page already there (a download stopped or broken off) is not asked again; a
  folder of the same gallery named otherwise is the one used.

The site moves pictures between its servers (``i``, ``i1`` … ``i7``; (v5.44.97) ``i1`` … ``i4`` by ``/api/v2/cdn``) and
kinds: a page is asked of the server that gave the last one, then of the others, then in the other kinds.  The journal never carries a cookie or a key: what a
server says is cut and cleaned (``sanitize_text``).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

_LOG = logging.getLogger("local_booru.manga")

SITE = "https://nhentai.net"
# (v5.44.97) the API of 2026 — the old «/api/gallery/{id}» answers 403 «Use new API https://nhentai.net/api/v2/docs»
API = SITE + "/api/v2/galleries/{id}"
CDN = SITE + "/api/v2/cdn"              # its picture servers: {"image_servers": [...], "thumb_servers": [...]}
IMAGE_SERVERS = ("https://i1.nhentai.net", "https://i2.nhentai.net", "https://i3.nhentai.net",
                 "https://i4.nhentai.net")     # when the API does not name them
PAGE = SITE + "/g/{id}/"
PICTURE = "https://{server}.nhentai.net/galleries/{media}/{n}{ext}"      # the pages of the old description
SERVERS = ("i", "i1", "i2", "i3", "i4", "i5", "i7")
RATE_WAIT = 60.0                 # (v5.44.97) «слишком часто» (429) of its API: waited this long (it says a minute)
RATE_TRIES = 2
RETRY_WAIT = 1.5                 # seconds before a busy server is asked again (more each time)
KINDS = {"j": ".jpg", "p": ".png", "g": ".gif", "w": ".webp"}
MAX_AT_ONCE = 50                 # galleries in one go
MAX_PAGES = 3000                 # pages of one gallery taken at most
MAX_JSON = 8 * 1024 * 1024
MAX_PICTURE = 64 * 1024 * 1024
PAGE_TRIES = 3                   # a page's server busy or cut off: asked again this many times
TIMEOUT = (10, 60)
NAME_MAX = 110                   # characters of the folder's name

_LINK = re.compile(r"^(?:https?://)?(?:www\.)?nhentai\.net/g/(\d{1,7})(?:/.*)?$", re.I)
_NUMBER = re.compile(r"^#?(\d{1,7})$")
_GALLERY_SCRIPT = re.compile(r"window\._gallery\s*=\s*JSON\.parse\(\s*(\"(?:[^\"\\]|\\.)*\")\s*\)", re.S)
# (v5.44.97) the page of 2026 (SvelteKit): the answers its scripts got, each a JSON of {status, headers, body}
_FETCHED = re.compile(r"<script\b[^>]*\bdata-sveltekit-fetched\b[^>]*>(.*?)</script>", re.S | re.I)
_THUMB = re.compile(r"//(t\d*)\.nhentai\.net/galleries/(\d+)/(\d+)t\.(\w+)", re.I)
# a link to a tag of the site: its kind is in its address (the same in every look of the site since its start)
_TAG_LINK = re.compile(r"^(?:https?://(?:www\.)?nhentai\.net)?/(tag|parody|character|artist|group|language|category)/"
                       r"([^/?#]+)/?$", re.I)
_COUNT = re.compile(r"\s*\d+(?:[.,]\d+)?\s*[kKmM]?\s*$")


class MangaError(Exception):
    """Said to the person as it is."""


class Forbidden(MangaError):
    """(v5.44.97) A gallery the program's rule leaves out: it depicts minors (``core/nhentai.py``)."""


# ── what was pasted ─────────────────────────────────────────────────────────────────
def gallery_ids(text: str) -> tuple[list[int], list[str]]:
    """(the galleries named — each once, in their order; the pieces not understood)."""
    found: list[int] = []
    bad: list[str] = []
    for piece in re.split(r"[\s,;]+", str(text or "")):
        piece = piece.strip().strip("<>\"'()")
        if not piece:
            continue
        match = _LINK.match(piece) or _NUMBER.match(piece)
        number = int(match.group(1)) if match else 0
        if number <= 0:
            bad.append(piece[:80])
        elif number not in found:
            found.append(number)
    return found, bad


# ── the description ─────────────────────────────────────────────────────────────────
@dataclass
class Gallery:
    id: int
    media_id: str
    kinds: list                          # the extension of each page, in order (".jpg", ".webp" …)
    english: str = ""
    japanese: str = ""
    pretty: str = ""
    data: dict = field(default_factory=dict)       # the description as the site gives it — meta.json
    server: str = "i"                    # the picture server that gave the last page
    paths: list = field(default_factory=list)      # (v5.44.97) the pages' paths of the API v2 («galleries/<media>/1.webp»)
    servers: list = field(default_factory=list)    # …and its picture servers (``/api/v2/cdn``)

    @property
    def title(self) -> str:
        return self.pretty or self.english or self.japanese or f"nhentai {self.id}"

    @property
    def pages(self) -> int:
        return len(self.kinds)


def gallery_of(data, number: int, servers=None) -> Gallery:
    """The gallery a description (the API's JSON) tells of — checked: its number, its pictures' number, its pages.
    (v5.44.97) The API v2's description too (``pages`` — their paths; ``servers`` — the picture servers the API names);
    a gallery the program's rule leaves out — :class:`Forbidden`."""
    from core import nhentai as N
    if not isinstance(data, dict):
        raise MangaError("Описание галереи непонятное")
    try:
        gid = int(data.get("id"))
    except (TypeError, ValueError):
        gid = number
    if gid != number:
        raise MangaError(f"Сайт отдал описание другой галереи ({gid} вместо {number})")
    media = str(data.get("media_id") or "").strip()
    if not re.fullmatch(r"[0-9]{1,12}", media):           # it goes into the pictures' addresses
        raise MangaError("В описании галереи нет номера её картинок")
    if N.forbidden_gallery(data):
        raise Forbidden(N.EXCLUDED)
    paths: list = []
    if isinstance(data.get("pages"), list):               # (v5.44.97) the API v2: each page's path
        for page in data["pages"][:MAX_PAGES]:
            path = str(page.get("path") or "").lstrip("/") if isinstance(page, dict) else ""
            if not N.path_ok(path, media):
                raise MangaError("В описании галереи непонятный адрес страницы")
            paths.append(path)
        kinds = [_ext_of(path) for path in paths]
    else:
        images = data.get("images") if isinstance(data.get("images"), dict) else {}
        pages = images.get("pages") if isinstance(images.get("pages"), list) else []
        kinds = [KINDS.get(str(p.get("t") or "j"), ".jpg") if isinstance(p, dict) else ".jpg"
                 for p in pages[:MAX_PAGES]]
    if not kinds:
        raise MangaError("В галерее нет страниц")
    title = data.get("title") if isinstance(data.get("title"), dict) else {}

    def text(key: str) -> str:
        return re.sub(r"\s+", " ", str(title.get(key) or "")).strip()
    if paths:
        bases = N.servers_ok(list(servers or ()), IMAGE_SERVERS, also=(_host(API), _host(CDN)))
        return Gallery(id=gid, media_id=media, kinds=kinds, english=text("english"), japanese=text("japanese"),
                       pretty=text("pretty"), data=data, server=bases[0], paths=paths, servers=bases)
    server = str(data.get("_server") or "")              # named by the page's thumbnails (``_from_thumbnails``)
    return Gallery(id=gid, media_id=media, kinds=kinds, english=text("english"), japanese=text("japanese"),
                   pretty=text("pretty"), data=data, server=server if re.fullmatch(r"i[0-9]{0,2}", server) else "i")


def _host(address: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(str(address or "")).hostname or "").lower()


def _ext_of(path: str) -> str:
    """A page's kind by its path («…/1.webp» → «.webp»; «…/2.webp.webp» — the same; one not known — «.jpg»)."""
    ext = "." + str(path).rsplit(".", 1)[-1].lower() if "." in str(path).rsplit("/", 1)[-1] else ""
    return ext if ext in set(KINDS.values()) | {".jpeg", ".avif"} else ".jpg"


def _json_of(text: str):
    """The JSON a browser shows: as it came, or inside its ``<pre>``."""
    text = str(text or "").strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    if "<pre" in text.lower():
        from bs4 import BeautifulSoup
        pre = BeautifulSoup(text, "html.parser").find("pre")
        if pre is not None:
            try:
                return json.loads(pre.get_text())
            except ValueError:
                return None
    return None


def gallery_from_text(text: str, number: int, servers=None) -> Gallery | None:
    """The gallery in what a browser got: the API's JSON, the gallery page's ``window._gallery = JSON.parse("…")``,
    (v5.44.97) the description among the answers the page's scripts got (``data-sveltekit-fetched``), or the page's
    thumbnails and tags (None — none of them)."""
    data = _json_of(text)
    if data is None:
        found = _GALLERY_SCRIPT.search(str(text or ""))
        if found:
            try:
                data = json.loads(json.loads(found.group(1)))
            except ValueError:
                data = None
    if data is None:
        data = _fetched_gallery(str(text or ""), number)
    if isinstance(data, dict) and data.get("media_id"):
        return gallery_of(data, number, servers)
    data = _from_thumbnails(str(text or ""), number)
    return gallery_of(data, number, servers) if data else None


def _fetched_gallery(text: str, number: int) -> dict | None:
    """(v5.44.97) The gallery's description among the answers the page's scripts were given (SvelteKit puts each in a
    ``<script type="application/json" data-sveltekit-fetched>`` as {status, headers, body — the answer's text})."""
    for block in _FETCHED.findall(text):
        try:
            answer = json.loads(block)
            body = json.loads(answer.get("body")) if isinstance(answer, dict) and isinstance(answer.get("body"), str) \
                else None
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("media_id") and str(body.get("id")) == str(number):
            return body
    return None


def _from_thumbnails(text: str, number: int) -> dict | None:
    """A description made of the gallery page itself: the thumbnails (``…/galleries/<media>/<n>t.<kind>``) give the
    pictures' number, the pages and their kinds; the headings the titles; the tag lines the tags."""
    if "/galleries/" not in text:
        return None
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, "html.parser")
    pages: dict[int, str] = {}
    media = server = ""
    for img in soup.find_all("img"):
        for attr in ("data-src", "src"):
            found = _THUMB.search(str(img.get(attr) or ""))
            if found:
                server = server or "i" + found.group(1)[1:]
                media = media or found.group(2)
                if found.group(2) == media:
                    pages.setdefault(int(found.group(3)), found.group(4).lower())
                break
    if not media or not pages:
        return None
    letters = {ext.lstrip("."): letter for letter, ext in KINDS.items()} | {"jpeg": "j"}
    heading = soup.select_one("h1.title") or soup.find("h1")
    pretty = heading.select_one(".pretty") if heading is not None else None
    second = soup.select_one("h2.title")
    tags = page_tags(soup)
    return {"id": number, "media_id": media, "_server": server,
            "title": {"english": heading.get_text(" ", strip=True) if heading is not None else "",
                      "japanese": second.get_text(" ", strip=True) if second is not None else "",
                      "pretty": pretty.get_text(" ", strip=True) if pretty is not None else ""},
            "images": {"pages": [{"t": letters.get(pages[n], "j")} for n in sorted(pages)]},
            "tags": tags, "num_pages": len(pages)}


def page_tags(soup) -> list[dict]:
    """The gallery's tags on its page: the links to tags in its tag lines (``#tags``; a page without it — any such
    link outside the menus) — the kind from the link's address (``/tag/…``, ``/artist/…``), the name from the link
    without the number of galleries beside it (``big breasts 239.4k``)."""
    from urllib.parse import unquote
    area = soup.select_one("#tags") or soup.select_one("#info") or soup
    tags: list[dict] = []
    seen: set = set()
    for link in area.find_all("a", href=True):
        found = _TAG_LINK.match(str(link["href"]).strip())
        if not found or link.find_parent(["nav", "header", "footer"]) is not None:
            continue
        named = link.select_one(".name")
        name = named.get_text(" ", strip=True) if named is not None else _COUNT.sub("", link.get_text(" ", strip=True))
        name = re.sub(r"\s+", " ", name).strip() or unquote(found.group(2)).replace("-", " ")
        key = (found.group(1).lower(), name.lower())
        if key not in seen:
            seen.add(key)
            tags.append({"type": key[0], "name": name})
    return tags


def _session():
    from core import video_meta_import as I
    return I._session(SITE)          # the site's cookies (taken from a browser) and a browser's name


def servers_of(session=None) -> list:
    """(v5.44.97) The picture servers the API names (``/api/v2/cdn``), else :data:`IMAGE_SERVERS`."""
    from core import nhentai as N
    from core import video_meta_import as I
    try:
        status, body, _final, _ctype = I._get(session or _session(), CDN, 64 * 1024, referer=SITE + "/")
        data = json.loads(I.decode_html(body, "utf-8")) if status == 200 else {}
    except (I.MetaImportError, ValueError):
        data = {}
    return N.servers_ok((data if isinstance(data, dict) else {}).get("image_servers"), IMAGE_SERVERS,
                        also=(_host(CDN),))


def description(number: int, *, say, window_fetch=None, session=None, memory: dict | None = None,
                servers=None, stop_check=None) -> Gallery:
    """The gallery's description from the site (``window_fetch(address)`` → (html, address): the program window's
    browser, for the site's check for robots).  ``memory`` — kept for the galleries of one download: an API the
    window's browser did not get is not asked of it again (the gallery's page is).  (v5.44.97) ``servers`` — the
    picture servers of the API v2 (:func:`servers_of`); «слишком часто» (429) — waited :data:`RATE_WAIT` and asked
    again (:data:`RATE_TRIES` times)."""
    from core import video_meta_import as I
    from core.redaction import sanitize_text
    address = API.format(id=number)
    session = session or _session()
    for attempt in range(RATE_TRIES + 1):
        try:
            status, body, _final, _ctype = I._get(session, address, MAX_JSON, referer=SITE + "/")
        except I.MetaImportError as exc:                # «Сайт не ответил: …», cleaned
            raise MangaError(sanitize_text(str(exc))[:240]) from exc
        if status != 429 or attempt == RATE_TRIES:
            break
        say(f"nhentai просит спрашивать реже — жду {int(RATE_WAIT)} с")
        if _rest(RATE_WAIT, stop_check):
            raise MangaError("остановлено")
    text = I.decode_html(body, "utf-8")
    if status == 404:
        raise MangaError(f"Галереи {number} на nhentai нет")
    if status == 429:
        raise MangaError("nhentai просит спрашивать реже — попробуйте через минуту")
    data = _json_of(text) if status == 200 else None
    if data is not None:
        return gallery_of(data, number, servers)
    guard = I.guard_of(status, text)
    say(f"Сайт проверяет, что заходит не робот ({guard})" if guard else
        f"Вместо описания nhentai ответил {f'кодом {status}' if status != 200 else 'страницей'}")
    if window_fetch is None:
        raise MangaError("nhentai не отдаёт описание программе напрямую, а окна программы, чтобы открыть его "
                         "браузером, нет — запустите скачивание из окна программы")
    why = ""
    places = [(address, "описание"), (PAGE.format(id=number), "страницу галереи")]
    if memory is not None and memory.get("api_in_window") is False:
        places = places[1:]
    for where, what in places:
        say(f"Открываю {what} браузером окна программы…")
        try:
            html, _final = window_fetch(where)
        except I.MetaImportError as exc:
            why = sanitize_text(str(exc))[:200]
            continue
        if '"error"' in html and ("does not exist" in html or "not found" in html.lower()):
            raise MangaError(f"Галереи {number} на nhentai нет")
        found = gallery_from_text(html, number, servers)
        if found is not None:
            return found
        if where == address and memory is not None:
            memory["api_in_window"] = False
        why = f"проверка на роботов ({I.guard_of(200, html)})" if I.guard_of(200, html) else "описания в ней нет"
    raise MangaError(f"Описание галереи не получено и браузером окна программы: {why}. Попробуйте позже")


def _rest(seconds: float, stop_check=None) -> bool:
    """Waits; True — stopped meanwhile."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_check is not None and stop_check():
            return True
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))
    return False


# (v5.44.97) the card of «Скачать → По ссылке»: a gallery's description kept a while (the card is opened again and
# again from «Лента»)
_DESCRIBED: dict = {}
_DESCRIBED_LOCK = threading.Lock()
DESCRIBED_TTL = 20 * 60
DESCRIBED_KEPT = 64


def describe(number: int) -> Gallery:
    """(v5.44.97) A gallery's description for its card — the API (no window's browser: the card does not wait for
    one; its thumbnails are on ``nhentai.THUMB_SERVERS``, its servers of pages are not asked), kept
    :data:`DESCRIBED_TTL`; :class:`MangaError` with words (:class:`Forbidden` — the program's rule)."""
    number = int(number)
    with _DESCRIBED_LOCK:
        kept = _DESCRIBED.get(number)
        if kept is not None and time.monotonic() - kept[0] < DESCRIBED_TTL:
            return kept[1]
    gallery = description(number, say=lambda _text: None, session=_session())
    with _DESCRIBED_LOCK:
        _DESCRIBED[number] = (time.monotonic(), gallery)
        while len(_DESCRIBED) > DESCRIBED_KEPT:
            _DESCRIBED.pop(min(_DESCRIBED, key=lambda k: _DESCRIBED[k][0]))
    return gallery


def forget_described() -> None:
    with _DESCRIBED_LOCK:
        _DESCRIBED.clear()


# ── the pages ─────────────────────────────────────────────────────────────────────────
_MAGIC = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"GIF87a", ".gif"), (b"GIF89a", ".gif"))


def picture_kind(data: bytes) -> str:
    """The extension the bytes are a picture of ("" — not a picture)."""
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    return ""


class _Refused(Exception):
    """The picture server answers with a check for robots — no page will come from it now."""


def _guard(status: int, body: bytes, ctype: str) -> str:
    """The check for robots an answer is: the service's name by its marks ("" — none: a page «404» is no check)."""
    if "html" not in ctype.lower():
        return ""
    from core import video_meta_import as I
    name = I.guard_of(status if status in (403, 429, 503) else 403, body[:200000].decode("utf-8", "replace"))
    return "" if name.startswith("ответ ") else name


def _ask(session, address: str, referer: str) -> tuple[int, bytes, str]:
    """(status, body, content type) — status 0: no answer (the server unknown, the line cut)."""
    import requests
    try:
        with session.get(address, timeout=TIMEOUT, stream=True, headers={"Referer": referer}) as answer:
            body = bytearray()
            for chunk in answer.iter_content(256 * 1024):
                body += chunk
                if len(body) > MAX_PICTURE:
                    return 413, b"", ""
            return answer.status_code, bytes(body), str(answer.headers.get("Content-Type") or "")
    except requests.RequestException as exc:
        _LOG.debug("nhentai page %s: %s", address, exc)
        return 0, b"", ""


def _tries_of(gallery: Gallery, n: int) -> list:
    """(server, address) to ask for page ``n``: its own kind on every server (the one that gave the last page first),
    then the other kinds on the first two.  (v5.44.97) The API v2's pages — their paths on its servers."""
    first = gallery.server
    own = gallery.kinds[n - 1]
    others = [ext for ext in dict.fromkeys(KINDS.values()) if ext != own]
    if gallery.paths:
        path = gallery.paths[n - 1]
        stem = path[:-len(path.rsplit("/", 1)[-1])] + path.rsplit("/", 1)[-1].split(".", 1)[0]
        servers = [first] + [s for s in gallery.servers if s != first] if first in gallery.servers else \
            list(gallery.servers)
        tries = [(server, f"{server}/{path}") for server in servers]
        return tries + [(server, f"{server}/{stem}{ext}") for ext in others for server in servers[:2]]
    servers = [first] + [s for s in SERVERS if s != first]
    tries = [(server, PICTURE.format(server=server, media=gallery.media_id, n=n, ext=own)) for server in servers]
    return tries + [(server, PICTURE.format(server=server, media=gallery.media_id, n=n, ext=ext))
                    for ext in others for server in servers[:2]]


def _page(session, gallery: Gallery, n: int, stop_check) -> tuple[bytes, str]:
    """Page ``n`` (from 1): its bytes and their kind — of the server that gave the last page, then of the others, then
    in the other kinds (MangaError with the last server's answer — none gave it; _Refused — a check for robots)."""
    referer = PAGE.format(id=gallery.id)
    first = gallery.server
    last = ""
    for server, address in _tries_of(gallery, n):
        for attempt in range(PAGE_TRIES):
            if stop_check():
                raise MangaError("остановлено")
            status, body, ctype = _ask(session, address, referer)
            if status == 200 and picture_kind(body):
                gallery.server = server
                return body, picture_kind(body)
            guard = _guard(status, body, ctype)
            if guard:
                raise _Refused(guard)
            # busy, or the line to the server that gave the pages so far cut: the same one again, a little later
            busy = status in (429, 500, 502, 503, 504) or (status == 0 and server == first)
            if busy and attempt + 1 < PAGE_TRIES:
                time.sleep(RETRY_WAIT * (attempt + 1))
                continue
            last = f"ответ {status}" if status else "сервер не ответил"
            break
    raise MangaError(last or "сервер не ответил")


# ── the folder ───────────────────────────────────────────────────────────────────────
def folder_for(root: Path, gallery: Gallery) -> tuple[Path, bool]:
    """(the gallery's folder in the manga folder, whether it is new): one already there for its number (named by an
    earlier download, or by hand: ``nhentai-691957 …``) is used as it is."""
    mark = re.compile(rf"^nhentai[-_ ]*{gallery.id}(?!\d)", re.I)
    try:
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and mark.match(entry.name):
                return entry, False
    except OSError:
        pass
    from core.video_download import safe_name
    title = safe_name(gallery.title, NAME_MAX)
    return root / (f"nhentai-{gallery.id} - {title}" if title else f"nhentai-{gallery.id}"), True


def _have(folder: Path, n: int, width: int) -> Path | None:
    for ext in dict.fromkeys(list(KINDS.values()) + [".jpeg", ".avif"]):
        path = folder / f"{n:0{width}d}{ext}"
        try:
            if path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def pages_there(folder: Path, pages: int) -> int:
    """(v5.44.116) How many of a gallery's ``pages`` its folder has as :func:`download` counts them (a page there is
    never asked again — «Скачать» of a whole gallery downloads nothing, the card says «✓ В папке»; of one cut short —
    the rest: «Докачать N»)."""
    width = max(3, len(str(pages)))
    return sum(1 for n in range(1, max(0, int(pages)) + 1) if _have(Path(folder), n, width) is not None)


def _write(path: Path, data: bytes) -> None:
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)


# ── the progress bar ─────────────────────────────────────────────────────────────────
_METER_LOCK = threading.Lock()
_METER: dict = {}


def meter() -> dict:
    """How the download goes: gallery k of n, its title, page i of its pages, bytes so far; ``items`` — each gallery
    of the download: {"number", "title", "pages", "page", "got", "had", "bytes", "state", "why", "folder"} (state:
    "wait", "reading", "pages", "done", "had" — all its pages were there, "failed", "stopped")."""
    with _METER_LOCK:
        out = dict(_METER)
        if "items" in out:
            out["items"] = [dict(item) for item in out["items"]]
        return out


def _set_meter(clear: bool = False, **values) -> None:
    with _METER_LOCK:
        if clear:
            _METER.clear()
        _METER.update(values)


# ── (v5.44.107) the queue ────────────────────────────────────────────────────────────────────
# The owner (7.11.2026): «почему нет очереди на скачивании манги» — «Скачать» while a download went was refused.  Now
# the galleries asked meanwhile join the download going, after the ones there (:func:`add`): they wait in it («в
# очереди») and come one after another; «убрать» takes one out before it begins (:func:`drop`).  What is left of the
# download when the program closes — its galleries not finished — is kept in ``manga_queue.json`` (the settings
# folder) and goes on at the next start (``webui/pages/manga_get.py``: :func:`kept_galleries`, :func:`await_restore`);
# «Остановить» (``USER_STOP``) keeps nothing.  (v5.44.116) The galleries' titles are kept beside them (the ones known:
# described by the download or by a card — :func:`kept_titles`), so the line «С прошлого запуска» names them; «Не
# продолжать» while they wait — they are not begun again (:func:`forget_restore`); a download begun while they wait
# keeps them in the file with its own (:func:`_persist`), and they are begun as read at the start (:func:`restored`).
KEEP_NAME = "manga_queue.json"
MAX_IN_RUN = 300                 # galleries in one download with its queue
RESTORE_AFTER = 30.0             # seconds after the start: the program's own start goes first
UNFINISHED = ("wait", "reading", "pages", "stopped")
USER_STOP = threading.Event()    # «Остановить»: what it stopped is not kept for the next run
_RUN_LOCK = threading.Lock()
_KEEP_LOCK = threading.Lock()    # one writer of manga_queue.json at a time (the download and a page's «Скачать»)
_RUN: dict = {"items": None, "open": False, "root": ""}
_RESTORED = threading.Event()
_PENDING: dict = {"numbers": [], "titles": {}, "at": 0.0}


def _item(number: int) -> dict:
    return {"number": number, "title": "", "pages": 0, "page": 0, "got": 0, "had": 0, "bytes": 0, "state": "wait",
            "why": "", "folder": ""}


def add(numbers: list) -> "list | None":
    """Galleries put into the download going, after the ones there — for each its place among the galleries waiting
    (0 — it is in the download already, begun or done).  None — no download takes more (none goes, or it is ending):
    the caller begins one of its own."""
    with _RUN_LOCK:
        if not _RUN["open"]:
            return None
        items = _RUN["items"]
        there = {x["number"] for x in items}
        for number in numbers:
            if number not in there and len(items) < MAX_IN_RUN:
                items.append(_item(number))
                there.add(number)
        waiting = [x["number"] for x in items if x["state"] == "wait"]
        places = [waiting.index(n) + 1 if n in waiting else 0 for n in numbers]
        snapshot = [dict(x) for x in items]
    _set_meter(galleries=len(snapshot), items=snapshot)
    _persist()
    return places


def drop(number: int) -> bool:
    """«Убрать»: a gallery waiting in the download going taken out of it (one begun stays)."""
    with _RUN_LOCK:
        items = _RUN["items"] if _RUN["open"] else None
        k = next((k for k, x in enumerate(items or []) if x["number"] == number and x["state"] == "wait"), -1)
        if k < 0:
            return False
        del items[k]
        snapshot = [dict(x) for x in items]
    _set_meter(galleries=len(snapshot), items=snapshot)
    _persist()
    return True


def waiting() -> list:
    """The galleries waiting in the download going: [{"number", "title"}]."""
    with _RUN_LOCK:
        return [{"number": x["number"], "title": x["title"]} for x in (_RUN["items"] or []) if x["state"] == "wait"]


def move(number: int, step: int) -> bool:
    """(v5.44.120) «↑» / «↓» of «Загрузки → Очередь»: a gallery waiting in the download going one place sooner
    (``step`` < 0) or later among the waiting ones (the download takes them in the order of its list); False — it does
    not wait (begun already) or it is first / last of them."""
    with _RUN_LOCK:
        items = _RUN["items"] if _RUN["open"] else None
        waits = [k for k, x in enumerate(items or []) if x["state"] == "wait"]
        here = next((n for n, k in enumerate(waits) if items[k]["number"] == number), -1)
        other = here + (1 if step > 0 else -1)
        if here < 0 or not 0 <= other < len(waits):
            return False
        a, b = waits[here], waits[other]
        items[a], items[b] = items[b], items[a]
        snapshot = [dict(x) for x in items]
    _set_meter(galleries=len(snapshot), items=snapshot)
    _persist()
    return True


# (v5.44.120) «Пауза» and «Пропустить галерею» of «Загрузки» (the owner's plan: «Пауза / Пропустить / Остановить всё» —
# the same for every kind): a pause stops between two pages (the page on its way comes), «Продолжить» goes on from
# the next page; a skip leaves the gallery going (its pages there stay — a next «Скачать» downloads the rest) and goes
# on with the next one: it did not come, «пропущена».
_PAUSE = threading.Event()
_SKIP = threading.Event()
SKIPPED = "пропущена"


def pause() -> None:
    _PAUSE.set()
    _set_meter(paused=True)


def resume() -> None:
    _PAUSE.clear()
    _set_meter(paused=False)


def paused() -> bool:
    return _PAUSE.is_set()


def skip() -> bool:
    """«Пропустить галерею»: the gallery being read or downloaded is left; False — none goes now."""
    with _RUN_LOCK:
        going = bool(_RUN["open"]) and any(x["state"] in ("reading", "pages") for x in _RUN["items"] or [])
    if going:
        _SKIP.set()
    return going


def _held(stop_check) -> bool:
    """While «Пауза»: waits (a skip or a stop ends the wait); True — stop."""
    while _PAUSE.is_set() and not stop_check() and not _SKIP.is_set():
        time.sleep(0.2)
    return stop_check()


def _keep_file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / KEEP_NAME


def _known_title(item: dict) -> str:
    """(v5.44.116) A gallery's title for the line «С прошлого запуска»: the download's (it read the description), else
    the card's (``describe`` keeps it a while) — "" when neither knows it (no question to the site for it)."""
    if item.get("title"):
        return str(item["title"])
    with _DESCRIBED_LOCK:
        kept = _DESCRIBED.get(int(item["number"]))
    return kept[1].title if kept is not None else ""


def _persist(final: bool = False) -> None:
    """``manga_queue.json``: the galleries of the download not finished and its folder (nothing after «Остановить»,
    nothing once it has ended); (v5.44.116) their titles known."""
    from core.file_safety import write_json_kept
    with _RUN_LOCK:
        items = [dict(x) for x in _RUN["items"] or []]
        root = _RUN["root"]
        waiting = [] if _RESTORED.is_set() else list(_PENDING["numbers"])     # (v5.44.116) the last run's, not
        waiting_titles = dict(_PENDING["titles"]) if waiting else {}           # begun again yet: kept with these
    left = [] if (final and USER_STOP.is_set()) else [x for x in items if x["state"] in UNFINISHED]
    titles = {str(x["number"]): title for x in left for title in (_known_title(x),) if title}
    numbers = [x["number"] for x in left]
    more = [n for n in waiting if n not in numbers]
    titles.update({str(n): waiting_titles[n] for n in more if waiting_titles.get(n)})
    kept = {"root": root if left else "", "galleries": (numbers + more)[:MAX_IN_RUN], "titles": titles}
    try:
        with _KEEP_LOCK:
            write_json_kept(_keep_file(), json.dumps(kept, ensure_ascii=False), dict)
    except OSError as exc:
        _LOG.warning("the queue of manga downloads not kept: %s", exc)


def kept_galleries() -> tuple[list, str]:
    """(the galleries the last run left unfinished, their folder) — ([], "") when none or it cannot be read."""
    from core.file_safety import read_json_kept
    try:
        data = read_json_kept(_keep_file(), dict, what="manga queue") or {}
    except OSError as exc:
        _LOG.warning("the queue of manga downloads cannot be read now: %s", exc)
        return [], ""
    numbers = [n for n in data.get("galleries") or [] if isinstance(n, int) and 0 < n < 10_000_000]
    return numbers[:MAX_IN_RUN], str(data.get("root") or "")


def kept_titles() -> dict:
    """(v5.44.116) {number: title} of the galleries ``manga_queue.json`` keeps (the ones whose title was known)."""
    from core.file_safety import read_json_kept
    try:
        data = read_json_kept(_keep_file(), dict, what="manga queue") or {}
    except OSError:
        return {}
    titles = data.get("titles") if isinstance(data.get("titles"), dict) else {}
    return {int(k): str(v)[:300] for k, v in titles.items() if str(k).isdigit() and str(v or "").strip()}


def await_restore() -> bool:
    """The galleries of the last run, before they are begun again (``RESTORE_AFTER`` from now) — kept for the page.
    False — none."""
    if _RESTORED.is_set():
        return False
    numbers, _root = kept_galleries()
    if not numbers:
        return False
    titles = kept_titles()
    with _RUN_LOCK:
        _PENDING.update(numbers=numbers, titles=titles, at=time.monotonic() + RESTORE_AFTER)
    return True


def restoring_public() -> dict | None:
    """{"titles", "in": seconds} of the galleries to be begun again soon — (v5.44.116) by their titles where known;
    None when none waits."""
    with _RUN_LOCK:
        if _RESTORED.is_set() or not _PENDING["numbers"]:
            return None
        numbers, known = _PENDING["numbers"], _PENDING["titles"]
        titles = [known.get(n) or f"nhentai {n}" for n in numbers[:6]] + (
            [f"ещё {len(numbers) - 6}"] if len(numbers) > 6 else [])
        return {"titles": titles, "in": max(0, int(round(_PENDING["at"] - time.monotonic())))}


def restored() -> list:
    """The galleries kept by the last run — once a run of the program (the next calls: [])."""
    with _RUN_LOCK:                                  # (v5.44.116) one of this and «Не продолжать», never both
        if _RESTORED.is_set():
            return []
        _RESTORED.set()
        numbers = list(_PENDING["numbers"])          # (v5.44.116) as read at the start: the file has the ones of now too
        _PENDING.update(numbers=[], titles={})
    return numbers or kept_galleries()[0]


def forget_restore() -> bool:
    """(v5.44.116) «Не продолжать»: the galleries of the last run, waiting to be begun again, are not —
    ``manga_queue.json`` keeps only the download going now (if one began meanwhile).  False — none waits (they have
    begun already)."""
    with _RUN_LOCK:
        if _RESTORED.is_set() or not _PENDING["numbers"]:
            return False
        _RESTORED.set()
        _PENDING.update(numbers=[], titles={})
        going = bool(_RUN["open"])
    if going:
        _persist()                                   # the download begun meanwhile keeps its own galleries
    else:                                            # (the items of a download ended before are not to be kept)
        from core.file_safety import write_json_kept
        try:
            with _KEEP_LOCK:
                write_json_kept(_keep_file(), json.dumps({"root": "", "galleries": [], "titles": {}}), dict)
        except OSError as exc:
            _LOG.warning("the queue of manga downloads not emptied: %s", exc)
    _LOG.info("manga downloads of the last run: not begun again («Не продолжать»)")
    return True


def _size(size: float) -> str:
    size = float(size or 0)
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} МБ".replace(".", ",")
    return f"{size / 1024:.0f} КБ"


# ── the whole of it ──────────────────────────────────────────────────────────────────
def download(numbers: list, root: Path, *, say, stop_check, window_fetch=None) -> dict:
    """The galleries, one after another, into the manga folder ``root``.  The result: {"done": [numbers], "failed":
    [numbers], "folders": [paths], "stopped": bool, "items": each gallery's line (see ``meter``)}."""
    from core.redaction import sanitize_text
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    session = _session()
    memory: dict = {}
    servers: list = []                    # (v5.44.97) the API's picture servers — asked once, with the first gallery
    done: list[int] = []
    failed: list[int] = []
    folders: list[str] = []
    stopped = False
    items = [_item(number) for number in dict.fromkeys(numbers)]
    if items:                      # (v5.44.107) the first one is under way already: not «в очереди» for add()
        items[0]["state"] = "reading"
    USER_STOP.clear()
    _PAUSE.clear()                 # (v5.44.120) a pause or a skip of the download before is not this one's
    _SKIP.clear()

    def halt() -> bool:            # what stops a gallery half-way: «Остановить» or (v5.44.120) «Пропустить галерею»
        return stop_check() or _SKIP.is_set()
    with _RUN_LOCK:                                   # (v5.44.107) the galleries asked meanwhile join it: add()
        _RUN.update(items=items, open=True, root=str(root))

    def mark(item: dict, **values) -> None:
        """A gallery's line of the page's list, then the list to the progress bar."""
        with _RUN_LOCK:
            item.update(values)
            snapshot = [dict(x) for x in items]
        _set_meter(items=snapshot, galleries=len(snapshot))
    _set_meter(clear=True, galleries=len(items), items=[dict(x) for x in items])
    _persist()
    def skipped(item: dict) -> None:
        """(v5.44.120) «Пропустить галерею»: this one did not come — «пропущена»; the next one goes."""
        _SKIP.clear()
        say(f"Галерея {item['number']}: пропущена — «Пропустить галерею»")
        failed.append(item["number"])
        mark(item, state="failed", why=SKIPPED)

    k = 0
    try:
        while True:
            if _held(stop_check) or stop_check():     # (v5.44.120) «Пауза» between two galleries
                stopped = True
                break
            _SKIP.clear()                             # asked between two: the next one is not skipped by it
            with _RUN_LOCK:                           # the next one waiting — taken at once («убрать» leaves it be)
                if k >= len(items):
                    _RUN["open"] = False
                    break
                item = items[k]
                item["state"] = "reading"
                number, total = item["number"], len(items)
            k += 1
            _set_meter(gallery=k, number=number, title="", page=0, pages=0, bytes=0)
            mark(item, state="reading")
            say(f"— Галерея {number} ({k} из {total})")
            try:
                gallery = description(number, say=say, window_fetch=window_fetch, session=session, memory=memory,
                                      servers=servers or None, stop_check=halt)
                if gallery.paths and not servers:     # (v5.44.97) the API v2's pages: its servers, asked once
                    servers = servers_of(session)
                    gallery.servers = list(servers)
                    gallery.server = servers[0]
            except MangaError as exc:
                if _SKIP.is_set() and not stop_check():
                    skipped(item)
                    continue
                say(f"Не скачана: {exc}")
                failed.append(number)
                mark(item, state="failed", why=sanitize_text(str(exc))[:200])
                continue
            if _SKIP.is_set() and not stop_check():   # asked while its description was read
                skipped(item)
                continue
            folder, new = folder_for(root, gallery)
            say(f"«{gallery.title}» — страниц: {gallery.pages}")
            say(f"Папка: {folder.name}" + (" (новая)" if new else " (уже есть — докачается в неё)"))
            _set_meter(title=gallery.title, pages=gallery.pages)
            mark(item, title=gallery.title, pages=gallery.pages, state="pages")
            try:
                folder.mkdir(parents=True, exist_ok=True)
                described = {key: value for key, value in gallery.data.items()
                             if not str(key).startswith("_") and value is not None}
                _write(folder / "meta.json", json.dumps(described, ensure_ascii=False, indent=1).encode("utf-8"))
            except OSError as exc:
                say(f"Не скачана: папка не записывается ({sanitize_text(str(exc))[:200]})")
                failed.append(number)
                mark(item, state="failed", why="папка не записывается")
                continue
            folders.append(str(folder))
            mark(item, folder=str(folder))
            width = max(3, len(str(gallery.pages)))
            got = had = 0
            size = 0
            missing: list[int] = []
            left = False                              # (v5.44.120) «Пропустить галерею»
            for n in range(1, gallery.pages + 1):
                _set_meter(page=n)
                mark(item, page=n, got=got, had=had, bytes=size)
                if _held(stop_check) or stop_check():   # (v5.44.120) «Пауза»: between two pages
                    stopped = True
                    break
                if _SKIP.is_set():
                    left = True
                    break
                if _have(folder, n, width) is not None:
                    had += 1
                    continue
                try:
                    data, ext = _page(session, gallery, n, halt)
                    _write(folder / f"{n:0{width}d}{ext}", data)
                except _Refused as exc:
                    say(f"Сервер картинок nhentai проверяет, что заходит не робот ({exc}) — остальные страницы не "
                        "скачаны. Попробуйте позже")
                    missing.extend(range(n, gallery.pages + 1))
                    break
                except MangaError as exc:
                    if stop_check():
                        stopped = True
                        break
                    if _SKIP.is_set():
                        left = True
                        break
                    say(f"Страница {n} не скачалась: {exc}")
                    missing.append(n)
                    continue
                except OSError as exc:                 # the disk full or the folder closed: the rest would not either
                    say(f"Страница {n} не записалась: {sanitize_text(str(exc))[:200]}")
                    missing.extend(range(n, gallery.pages + 1))
                    break
                got += 1
                size += len(data)
                _set_meter(bytes=size)
            line = f"Скачано страниц: {got}" + (f", уже были: {had}" if had else "") + f" из {gallery.pages}"
            say(line + (f" ({_size(size)})" if size else ""))
            mark(item, got=got, had=had, bytes=size)
            if stopped:
                say("Остановлено — при следующем скачивании галерея докачается")
                mark(item, state="stopped")
                break
            if left:
                skipped(item)
                _persist()
                continue
            if missing:
                say(f"Не скачались страницы: {', '.join(map(str, missing[:20]))}" + (" …" if len(missing) > 20 else ""))
                failed.append(number)
                mark(item, state="failed", why="не скачались страницы: " + ", ".join(map(str, missing[:12]))
                     + (" …" if len(missing) > 12 else ""))
            else:
                done.append(number)
                mark(item, state="had" if had == gallery.pages else "done")
            _persist()
    finally:
        with _RUN_LOCK:
            _RUN["open"] = False
        _PAUSE.clear()
        _SKIP.clear()
        _persist(final=True)
        _set_meter(clear=True)
    from core import manga_library
    manga_library.LIBRARY.invalidate()
    total = f"Итог: скачано галерей: {len(done)}" + (f", не скачано: {len(failed)}" if failed else "")
    say(total + (" (остановлено)" if stopped else ""))
    return {"done": done, "failed": failed, "folders": folders, "stopped": stopped, "items": items}
