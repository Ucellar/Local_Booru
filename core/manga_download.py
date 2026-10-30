"""Manga from nhentai by its link or number (the tab «Манга» → «Скачать с nhentai», ``/manga/get``).

* :func:`gallery_ids` — what the person pasted: links ``https://nhentai.net/g/691957/`` (a page of it too) and bare
  numbers (``691957``, ``#691957``), several at once;
* :func:`description` — the gallery's description from the site's API (its pages and their kinds, titles, tags);
  a check for robots (Cloudflare) → the program window's browser opens the API address, then the gallery's page
  (its ``window._gallery`` carries the same description; failing that its page thumbnails and tags are read);
* :func:`download` — the galleries one after another into the manga folder, each as ``nhentai-<number> - <title>``
  with pages ``001.jpg`` … and the description as ``meta.json`` (``core/manga_library.py`` reads it: the titles, the
  tags by kind, the link back).  A page already there (a download stopped or broken off) is not asked again; a
  folder of the same gallery named otherwise is the one used.

The site moves pictures between its servers (``i``, ``i1`` … ``i7``) and kinds: a page is asked of the server that gave
the last one, then of the others, then in the other kinds.  The journal never carries a cookie or a key: what a
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
API = SITE + "/api/gallery/{id}"
PAGE = SITE + "/g/{id}/"
PICTURE = "https://{server}.nhentai.net/galleries/{media}/{n}{ext}"
SERVERS = ("i", "i1", "i2", "i3", "i4", "i5", "i7")
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
_THUMB = re.compile(r"//(t\d*)\.nhentai\.net/galleries/(\d+)/(\d+)t\.(\w+)", re.I)
# a link to a tag of the site: its kind is in its address (the same in every look of the site since its start)
_TAG_LINK = re.compile(r"^(?:https?://(?:www\.)?nhentai\.net)?/(tag|parody|character|artist|group|language|category)/"
                       r"([^/?#]+)/?$", re.I)
_COUNT = re.compile(r"\s*\d+(?:[.,]\d+)?\s*[kKmM]?\s*$")


class MangaError(Exception):
    """Said to the person as it is."""


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

    @property
    def title(self) -> str:
        return self.pretty or self.english or self.japanese or f"nhentai {self.id}"

    @property
    def pages(self) -> int:
        return len(self.kinds)


def gallery_of(data, number: int) -> Gallery:
    """The gallery a description (the API's JSON) tells of — checked: its number, its pictures' number, its pages."""
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
    images = data.get("images") if isinstance(data.get("images"), dict) else {}
    pages = images.get("pages") if isinstance(images.get("pages"), list) else []
    kinds = [KINDS.get(str(p.get("t") or "j"), ".jpg") if isinstance(p, dict) else ".jpg" for p in pages[:MAX_PAGES]]
    if not kinds:
        raise MangaError("В галерее нет страниц")
    title = data.get("title") if isinstance(data.get("title"), dict) else {}

    def text(key: str) -> str:
        return re.sub(r"\s+", " ", str(title.get(key) or "")).strip()
    server = str(data.get("_server") or "")              # named by the page's thumbnails (``_from_thumbnails``)
    return Gallery(id=gid, media_id=media, kinds=kinds, english=text("english"), japanese=text("japanese"),
                   pretty=text("pretty"), data=data, server=server if re.fullmatch(r"i[0-9]{0,2}", server) else "i")


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


def gallery_from_text(text: str, number: int) -> Gallery | None:
    """The gallery in what a browser got: the API's JSON, the gallery page's ``window._gallery = JSON.parse("…")``, or
    the page's thumbnails and tags (None — none of them)."""
    data = _json_of(text)
    if data is None:
        found = _GALLERY_SCRIPT.search(str(text or ""))
        if found:
            try:
                data = json.loads(json.loads(found.group(1)))
            except ValueError:
                data = None
    if isinstance(data, dict) and data.get("media_id"):
        return gallery_of(data, number)
    data = _from_thumbnails(str(text or ""), number)
    return gallery_of(data, number) if data else None


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


def description(number: int, *, say, window_fetch=None, session=None, memory: dict | None = None) -> Gallery:
    """The gallery's description from the site (``window_fetch(address)`` → (html, address): the program window's
    browser, for the site's check for robots).  ``memory`` — kept for the galleries of one download: an API the
    window's browser did not get is not asked of it again (the gallery's page is)."""
    from core import video_meta_import as I
    from core.redaction import sanitize_text
    address = API.format(id=number)
    session = session or _session()
    try:
        status, body, _final, _ctype = I._get(session, address, MAX_JSON, referer=SITE + "/")
    except I.MetaImportError as exc:                # «Сайт не ответил: …», cleaned
        raise MangaError(sanitize_text(str(exc))[:240]) from exc
    text = I.decode_html(body, "utf-8")
    if status == 404:
        raise MangaError(f"Галереи {number} на nhentai нет")
    data = _json_of(text) if status == 200 else None
    if data is not None:
        return gallery_of(data, number)
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
        if '"error"' in html and "does not exist" in html:
            raise MangaError(f"Галереи {number} на nhentai нет")
        found = gallery_from_text(html, number)
        if found is not None:
            return found
        if where == address and memory is not None:
            memory["api_in_window"] = False
        why = f"проверка на роботов ({I.guard_of(200, html)})" if I.guard_of(200, html) else "описания в ней нет"
    raise MangaError(f"Описание галереи не получено и браузером окна программы: {why}. Попробуйте позже")


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


def _page(session, gallery: Gallery, n: int, stop_check) -> tuple[bytes, str]:
    """Page ``n`` (from 1): its bytes and their kind — of the server that gave the last page, then of the others, then
    in the other kinds (MangaError with the last server's answer — none gave it; _Refused — a check for robots)."""
    referer = PAGE.format(id=gallery.id)
    first = gallery.server
    servers = [first] + [s for s in SERVERS if s != first]
    own = gallery.kinds[n - 1]
    tries = [(server, own) for server in servers]
    tries += [(server, ext) for ext in dict.fromkeys(KINDS.values()) if ext != own for server in servers[:2]]
    last = ""
    for server, ext in tries:
        address = PICTURE.format(server=server, media=gallery.media_id, n=n, ext=ext)
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
    done: list[int] = []
    failed: list[int] = []
    folders: list[str] = []
    stopped = False
    items = [{"number": number, "title": "", "pages": 0, "page": 0, "got": 0, "had": 0, "bytes": 0, "state": "wait",
              "why": "", "folder": ""} for number in numbers]

    def mark(item: dict, **values) -> None:
        """A gallery's line of the page's list, then the list to the progress bar."""
        item.update(values)
        _set_meter(items=[dict(x) for x in items])
    _set_meter(clear=True, galleries=len(numbers), items=[dict(x) for x in items])
    try:
        for k, number in enumerate(numbers, 1):
            item = items[k - 1]
            if stop_check():
                stopped = True
                break
            _set_meter(gallery=k, number=number, title="", page=0, pages=0, bytes=0)
            mark(item, state="reading")
            say(f"— Галерея {number} ({k} из {len(numbers)})")
            try:
                gallery = description(number, say=say, window_fetch=window_fetch, session=session, memory=memory)
            except MangaError as exc:
                say(f"Не скачана: {exc}")
                failed.append(number)
                mark(item, state="failed", why=sanitize_text(str(exc))[:200])
                continue
            folder, new = folder_for(root, gallery)
            say(f"«{gallery.title}» — страниц: {gallery.pages}")
            say(f"Папка: {folder.name}" + (" (новая)" if new else " (уже есть — докачается в неё)"))
            _set_meter(title=gallery.title, pages=gallery.pages)
            mark(item, title=gallery.title, pages=gallery.pages, state="pages")
            try:
                folder.mkdir(parents=True, exist_ok=True)
                described = {key: value for key, value in gallery.data.items() if not str(key).startswith("_")}
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
            for n in range(1, gallery.pages + 1):
                _set_meter(page=n)
                mark(item, page=n, got=got, had=had, bytes=size)
                if stop_check():
                    stopped = True
                    break
                if _have(folder, n, width) is not None:
                    had += 1
                    continue
                try:
                    data, ext = _page(session, gallery, n, stop_check)
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
            if missing:
                say(f"Не скачались страницы: {', '.join(map(str, missing[:20]))}" + (" …" if len(missing) > 20 else ""))
                failed.append(number)
                mark(item, state="failed", why="не скачались страницы: " + ", ".join(map(str, missing[:12]))
                     + (" …" if len(missing) > 12 else ""))
            else:
                done.append(number)
                mark(item, state="had" if had == gallery.pages else "done")
    finally:
        _set_meter(clear=True)
    from core import manga_library
    manga_library.LIBRARY.invalidate()
    total = f"Итог: скачано галерей: {len(done)}" + (f", не скачано: {len(failed)}" if failed else "")
    say(total + (" (остановлено)" if stopped else ""))
    return {"done": done, "failed": failed, "folders": folders, "stopped": stopped, "items": items}
