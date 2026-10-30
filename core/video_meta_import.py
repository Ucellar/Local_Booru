"""«Описание с сайта» of a series on the tab «Видео»: the page of the title, read by ``core/video_meta``, written next
to the videos — ``info.json`` and the poster (``core/video_library`` reads both).

Where the page comes from:

* its address — a plain request (the program's own cookies of the site from «Сайты и ключи» go with it); a site that
  answers with a check for robots (Cloudflare, DDoS-Guard) is asked again through the program window's browser, which
  passes such checks the way a browser does (``ui/page_fetch.py``, only in the program window);
* a saved copy of the page: ``.html``, ``.mhtml`` (Chrome's «веб-страница, один файл») or a ``.zip`` of a saved page
  (saveweb2zip and the like) — its pictures are taken from the copy, without the network.

The poster: of the pictures the page names (``poster_urls``), one shaped like a poster (taller than wide, at least
150×200) before any other — a frame of the video (wide, often with the site's mark) only when there is no poster —, and
of those the largest that opens as a picture; one from the copy first, the site's only when the copy has no poster of at
least 600 px.  A poster the person put there (``poster.jpg``
not written by this import — ``info.json`` "poster_file" and "poster_md5") is kept unless «заменить обложку» was
asked; even then a picture of the person's under the same name is renamed aside (``poster.old-<time>.jpg``), never
written over.  ``info.json`` is read again just before it is written: the person's own keys stay, a file that is not
JSON is kept as ``info.json.broken-<time>``, one that cannot be opened stops the import.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

from core import video_library, video_meta

_LOG = logging.getLogger("local_booru.video")

MAX_PAGE = 15 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024
MAX_SAVED = 200 * 1024 * 1024         # a saved copy as it is uploaded (a .zip with its fonts and pictures)
GOOD_POSTER = 600                     # px: a poster this tall from the copy is not looked for on the site
POSTER_SHAPE = 1.15                   # a poster is at least this much taller than wide (a video frame is wider)
POSTER_MIN = (150, 200)               # px: smaller pictures are not taken for a poster
SITE_POSTERS = 6                      # pictures asked of the site at most
TIMEOUT = (10, 40)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/136.0.0.0 Safari/537.36")
SAVED_EXTS = (".html", ".htm", ".mhtml", ".mht", ".zip")
INFO_LOCK = threading.Lock()          # info.json is read again and written under it (an import, the person's mark)


class MetaImportError(Exception):
    """Said to the person as it is."""


class Blocked(MetaImportError):
    pass


@dataclass
class SavedPage:
    html: str
    url: str = ""                                  # the page's address, when known
    files: dict = field(default_factory=dict)      # a saved copy's files: key (see ``_key``) -> bytes
    origin: str = ""                               # "сайт", "окно программы", "файл …"

    def local(self, link: str) -> bytes | None:
        """A picture of the saved copy by the address the page has for it."""
        if not self.files:
            return None
        for key in _keys(link, self.url):
            if key in self.files:
                return self.files[key]
        return None


def _key(path: str) -> str:
    return unquote(str(path or "")).replace("\\", "/").lstrip("./").lower()


def _keys(link: str, base: str) -> list[str]:
    link = str(link or "").strip()
    out = [_key(link.split("?", 1)[0].split("#", 1)[0])]
    if link.startswith(("http://", "https://", "//")):
        out.append(link.lower())                   # an .mhtml keeps its parts by their full address
        parsed = urlparse(link if not link.startswith("//") else "https:" + link)
        out.append(_key(parsed.path))
        out.append(_key(PurePosixPath(parsed.path).name))
    elif base:
        out.append(urljoin(base, link).lower())
    return [k for k in out if k]


# ── a saved copy ───────────────────────────────────────────────────────────────
def page_from_file(name: str, data: bytes) -> SavedPage:
    ext = Path(str(name or "")).suffix.lower()
    if len(data) > MAX_SAVED:
        raise MetaImportError(f"Файл больше {MAX_SAVED // (1024 * 1024)} МБ — это не сохранённая страница")
    if ext == ".zip" or data[:4] == b"PK\x03\x04":
        return _from_zip(data, name)
    head = data[:4096].lower()
    if ext in (".mhtml", ".mht") or (b"mime-version:" in head and b"multipart/related" in head):
        return _from_mhtml(data, name)
    if ext in (".html", ".htm", "") or b"<html" in data[:4096].lower():
        html = decode_html(data)
        return SavedPage(html=html, url=saved_from(html), origin=f"файл {name}")
    raise MetaImportError("Нужна сохранённая страница: .html, .mhtml или .zip")


_SAVED_FROM = re.compile(r"<!--\s*saved from url=\(\d+\)\s*(https?://[^\s>]+?)\s*-->", re.I)


def saved_from(html: str) -> str:
    """The address a browser wrote into a page it saved (``<!-- saved from url=(0038)https://… -->``), or ""."""
    found = _SAVED_FROM.search((html or "")[:4096])
    return found.group(1) if found else ""


def _from_zip(data: bytes, name: str) -> SavedPage:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise MetaImportError(f"Архив не открывается: {exc}") from exc
    members = [m for m in archive.infolist() if not m.is_dir()]
    pages = [m for m in members if m.filename.lower().endswith((".html", ".htm"))]
    if not pages:
        raise MetaImportError("В архиве нет страницы .html")
    page = min(pages, key=lambda m: (m.filename.count("/"), Path(m.filename).name.lower() != "index.html",
                                     m.filename.lower()))
    if page.file_size > MAX_PAGE:
        raise MetaImportError("Страница в архиве слишком большая")
    html = decode_html(archive.read(page))
    folder = str(PurePosixPath(page.filename).parent)
    url = saved_from(html)
    files = {}
    for member in members:
        if member is page or member.file_size > MAX_IMAGE:
            continue
        if Path(member.filename).suffix.lower() not in video_library.IMAGE_EXTS:
            continue
        rel = member.filename
        if folder not in ("", ".") and rel.startswith(folder + "/"):
            rel = rel[len(folder) + 1:]
        try:
            files[_key(rel)] = archive.read(member)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:     # RuntimeError: an encrypted member
            _LOG.debug("saved page %s: %s not read (%s)", name, member.filename, exc)
    return SavedPage(html=html, url=url, files=files, origin=f"файл {name}")


def _from_mhtml(data: bytes, name: str) -> SavedPage:
    message = email.message_from_bytes(data, policy=email.policy.compat32)
    html, url, files = "", "", {}
    for part in message.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        location = str(part.get("Content-Location") or "")
        payload = part.get_payload(decode=True) or b""
        if ctype == "text/html" and not html:
            html = decode_html(payload, part.get_content_charset() or "")
            url = location if location.startswith(("http://", "https://")) else ""
        elif ctype.startswith("image/") and len(payload) <= MAX_IMAGE:
            if location:
                files[location.lower()] = payload
                files[_key(urlparse(location).path)] = payload
            content_id = str(part.get("Content-ID") or "").strip("<>")
            if content_id:
                files["cid:" + content_id.lower()] = payload
    if not html:
        raise MetaImportError("В файле .mhtml нет страницы")
    return SavedPage(html=html, url=url, files=files, origin=f"файл {name}")


def decode_html(data: bytes, charset: str = "") -> str:
    head = data[:4096].decode("ascii", "replace")
    found = re.search(r"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", head, re.I)
    for encoding in (charset, found.group(1) if found else "", "utf-8"):
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("cp1251", "replace")


# ── from the site ─────────────────────────────────────────────────────────────────
_GUARDS = (("Cloudflare", ("cf-browser-verification", "challenge-platform", "cf_chl_", "Just a moment...",
                           "Attention Required! | Cloudflare", "cf-turnstile")),
           ("DDoS-Guard", ("ddos-guard", "DDoS-Guard", "__ddg")))


def guard_of(status: int, html: str) -> str:
    """The check for robots this answer is ("" — none): the name of the service, or the HTTP status."""
    for name, marks in _GUARDS:
        if any(mark in html for mark in marks) and (status in (403, 429, 503) or "<h1" not in html.lower()):
            return name
    if status in (403, 429, 503):
        return f"ответ {status}"
    return ""


def check_url(url: str) -> str:
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")) or not urlparse(url).hostname:
        raise MetaImportError("Нужна ссылка на страницу тайтла, начиная с https://")
    return url


def _session(url: str):
    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                                      "image/webp,*/*;q=0.8"})
    host = (urlparse(url).hostname or "").removeprefix("www.")
    try:
        from core.tagger.cookies_io import load_cookie_bundle_for_host, load_txt_cookiejar_for_host
        cookies, agent = load_cookie_bundle_for_host(host)
        for cookie in cookies:
            if cookie.get("name"):
                session.cookies.set(str(cookie["name"]), str(cookie.get("value") or ""),
                                    domain=str(cookie.get("domain") or host), path=str(cookie.get("path") or "/"))
        if agent:
            session.headers["User-Agent"] = str(agent)
        jar, _why = load_txt_cookiejar_for_host(host)
        if jar is not None:
            for cookie in jar:
                session.cookies.set_cookie(cookie)
    except (OSError, ValueError) as exc:
        _LOG.debug("video description: cookies of %s not read (%s)", host, exc)
    return session


def _get(session, url: str, limit: int, *, referer: str = "") -> tuple[int, bytes, str, str]:
    """(status, body, final address, content type) — at most ``limit`` bytes."""
    import requests
    headers = {"Referer": referer} if referer else {}
    try:
        with session.get(url, timeout=TIMEOUT, stream=True, headers=headers, allow_redirects=True) as answer:
            body = bytearray()
            for chunk in answer.iter_content(64 * 1024):
                body += chunk
                if len(body) > limit:
                    raise MetaImportError(f"Ответ больше {limit // (1024 * 1024)} МБ")
            return answer.status_code, bytes(body), answer.url, str(answer.headers.get("Content-Type") or "")
    except requests.RequestException as exc:
        from core.redaction import sanitize_text
        raise MetaImportError(f"Сайт не ответил: {sanitize_text(str(exc))[:300]}") from exc


def page_from_url(url: str, *, progress=None, window_fetch=None) -> SavedPage:
    """The page by its address; a check for robots — through the program window's browser (``window_fetch(url)``
    → (html, final url) or raises :class:`MetaImportError`), else :class:`Blocked`."""
    url = check_url(url)
    say = progress or (lambda _text: None)
    session = _session(url)
    status, body, final, ctype = _get(session, url, MAX_PAGE)
    charset = re.search(r"charset=([\w-]+)", ctype, re.I)
    html = decode_html(body, charset.group(1) if charset else "")
    guard = guard_of(status, html)
    if not guard and status >= 400:
        raise MetaImportError(f"Сайт ответил {status}: такой страницы нет или она закрыта")
    if not guard:
        say(f"Страница получена: {len(body) // 1024} КБ")
        return SavedPage(html=html, url=final, origin="сайт")
    say(f"Сайт проверяет, что заходит не робот ({guard})")
    if window_fetch is None:
        raise Blocked(f"Сайт проверяет, что заходит не робот ({guard}). Откройте страницу в браузере, сохраните её "
                      "(Ctrl+S — «Веб-страница, один файл» или полностью) и загрузите файл здесь")
    say("Открываю страницу браузером окна программы…")
    html, final = window_fetch(url)
    guard = guard_of(200, html)
    if guard:
        raise Blocked(f"Проверка сайта на роботов ({guard}) не пропустила и браузер окна программы. Откройте "
                      "страницу в своём браузере, сохраните её (Ctrl+S) и загрузите файл здесь")
    say(f"Страница получена браузером окна программы: {len(html) // 1024} КБ")
    return SavedPage(html=html, url=final or url, origin="окно программы")


# ── writing it ─────────────────────────────────────────────────────────────────────
def info_path(series: video_library.Series) -> Path:
    path = Path(series.path)
    return video_library.single_info_path(path) if series.single else path / video_library.INFO_NAME


def _poster_target(series: video_library.Series, ext: str) -> Path:
    path = Path(series.path)
    return path.with_name(f"{path.stem}-poster{ext}") if series.single else path / f"poster{ext}"


def _picture(data: bytes) -> tuple[int, int, str] | None:
    """(width, height, extension to keep) of picture bytes, or None when they are not a picture."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            kind = (im.format or "").upper()
    except Exception:  # Pillow raises many kinds of errors for bytes that are not a picture
        return None
    if width < 64 or height < 64:
        return None
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(kind, "")
    return width, height, ext


def _as_jpeg(data: bytes) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=92)
        return out.getvalue()


def _saved_folder(link: str) -> str:
    """«Название_files» when the link is a picture in the folder a browser saved the page's files to, else ""."""
    link = unquote(str(link or "").strip())
    if link.startswith(("http://", "https://", "//")):
        return ""
    found = re.search(r"(?:^|[/\\])([^/\\]*_files)[/\\]", link)
    return found.group(1) if found else ""


def poster_rank(width: int, height: int) -> tuple[int, int]:
    """How good a picture is for a poster: shaped like one (taller than wide, not tiny) first, then the larger."""
    shaped = height >= width * POSTER_SHAPE and width >= POSTER_MIN[0] and height >= POSTER_MIN[1]
    return (1 if shaped else 0), width * height


def _choose_poster(page: SavedPage, candidates: list[str], say, stop_check) -> tuple[bytes, str, str] | None:
    """(bytes, extension, where from) of the best picture among the candidates (``poster_rank``)."""
    found = []
    for link in candidates:
        data = page.local(link)
        if data:
            shape = _picture(data)
            if shape:
                found.append((poster_rank(shape[0], shape[1]), shape[1], data, shape[2], "из сохранённой страницы"))
    if any(f[0][0] and f[1] >= GOOD_POSTER for f in found):
        best = max(found, key=lambda f: f[0])
        return best[2], best[3], best[4]
    session = None
    left_behind = ""
    for link in candidates[:SITE_POSTERS]:
        if stop_check():
            break
        folder = _saved_folder(link)
        if folder:                         # the folder a browser saves the page's pictures to: not on the site
            left_behind = left_behind or folder
            continue
        address = video_meta.poster_address(link, page.url)
        if not address:
            continue
        try:
            session = session or _session(address)
            status, data, _final, _ctype = _get(session, address, MAX_IMAGE, referer=page.url)
        except MetaImportError as exc:
            say(f"Картинка {address} не скачалась: {exc}")
            continue
        shape = _picture(data) if status < 400 else None
        if shape:
            found.append((poster_rank(shape[0], shape[1]), shape[1], data, shape[2], address))
        else:
            say(f"Картинка {address}: не картинка (ответ {status})")
    if not found and left_behind:
        say(f"Обложка — в папке «{left_behind}» рядом с сохранённой страницей, а в загрузке её нет: загрузите "
            "страницу по ссылке или архивом .zip вместе с этой папкой")
    if not found:
        return None
    best = max(found, key=lambda f: f[0])
    return best[2], best[3], best[4]


def poster_of(page: SavedPage, *, say=None, stop_check=None) -> tuple[bytes, str, str] | None:
    """The poster the page names (as an import would choose it — ``_choose_poster``): «Скачать с сайта» shows it
    with the list of files and hands it to the import of the description (``import_into(chosen_poster=…)``), so it is not
    asked of the site twice."""
    candidates = video_meta.parse_page(page.html, page.url).get("poster_urls") or []
    if not candidates:
        return None
    return _choose_poster(page, candidates, say or (lambda _text: None), stop_check or (lambda: False))


def _ours(poster: Path, info: dict) -> bool:
    """The poster is the one an import wrote (its name, and its bytes when they were noted): another one, even under
    the same name, is the person's own."""
    if poster.name != str(info.get("poster_file") or ""):
        return False
    noted = str(info.get("poster_md5") or "")
    if not noted:
        return True
    try:
        return hashlib.md5(poster.read_bytes()).hexdigest() == noted
    except OSError:
        return False


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _aside(path: Path) -> Path:
    """Rename the person's file out of the way (``poster.old-<time>.jpg``, ``info.json.broken-<time>``) — never
    written over."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if path.suffix.lower() == ".json":
        kept = path.with_name(f"{path.name}.broken-{stamp}")
    else:
        kept = path.with_name(f"{path.stem}.old-{stamp}{path.suffix}")
    try:
        path.replace(kept)
    except OSError as exc:
        raise MetaImportError(f"Файл {path.name} не удалось отложить (он остался как был): {exc}") from exc
    return kept


def _current_info(target: Path, say) -> dict:
    """The description now in the folder, read again (the list of series may be a minute old).  Text that is not a
    description (written by hand with a mistake) is kept beside as ``info.json.broken-<time>``; a file that cannot be
    opened now stops the import — it is not written over."""
    try:
        if target.stat().st_size > video_library.MAX_INFO_BYTES:
            raise ValueError("too large")
        text = target.read_text(encoding="utf-8-sig")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError("not an object")
    except FileNotFoundError:
        return {}
    except ValueError:                    # UnicodeDecodeError too
        kept = _aside(target)
        say(f"Прежний {target.name} не читается как описание — оставлен как {kept.name}")
        return {}
    except OSError as exc:
        raise MetaImportError(f"Описание {target.name} сейчас не открыть (его держит другая программа?): {exc}. "
                              "Попробуйте ещё раз") from exc


def set_rating(series: video_library.Series, value: int) -> int:
    """The person's own mark of a series, 1…10 (0 — taken back): written into its ``info.json`` (read again, the
    rest kept as it is).  Raises MetaImportError when the description cannot be opened now."""
    value = int(value)
    if not 0 <= value <= 10:
        raise MetaImportError("Оценка — от 1 до 10")
    target = info_path(series)
    with INFO_LOCK:
        info = _current_info(target, lambda text: _LOG.info("%s", text))
        if value:
            info["my_rating"] = value
        else:
            info.pop("my_rating", None)
        if not info and not target.exists():
            return value
        try:
            _write(target, json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError as exc:
            raise MetaImportError(f"Оценка не записалась в папку: {exc}") from exc
    video_library.LIBRARY.invalidate()
    return value


def import_into(series: video_library.Series, page: SavedPage, *, progress=None, stop_check=None,
                replace_poster: bool = False, chosen_poster: tuple | None = None) -> dict:
    """Read the page and write the series' ``info.json`` and poster.  {"ok", "title", "poster", "fields"}.
    ``chosen_poster`` — (bytes, extension, where from) chosen already (``poster_of``): not asked of the site again."""
    say = progress or (lambda _text: None)
    stop = stop_check or (lambda: False)
    meta = video_meta.parse_page(page.html, page.url)
    candidates = meta.pop("poster_urls", [])
    if not meta.get("title") and not meta.get("description"):
        raise MetaImportError("На странице нет ни названия, ни описания — это точно страница тайтла?")
    say(f"Название: {meta.get('title') or '—'}")
    fields = [video_meta.FIELD_TITLES[k] for k in video_meta.FIELD_ORDER if meta.get(k)]
    if fields:
        say("Нашлось: " + ", ".join(fields))
    if meta.get("files"):
        say(f"Файлов у плеера сайта: {len(meta['files'])}")
    target = info_path(series)
    old = _current_info(target, say)
    poster_note = ""
    current_poster = Path(series.poster) if series.poster else None
    ours = current_poster is not None and _ours(current_poster, old)
    if candidates and (current_poster is None or ours or replace_poster):
        chosen = chosen_poster if chosen_poster is not None else _choose_poster(page, candidates, say, stop)
        if chosen is not None:
            data, ext, where = chosen
            if not ext:
                data, ext = _as_jpeg(data), ".jpg"
            poster = _poster_target(series, ext)
            if poster.exists() and not (ours and poster == current_poster):
                kept = _aside(poster)          # the person's own picture of this name: «заменить обложку» keeps it
                say(f"Прежняя обложка {poster.name} оставлена в папке как {kept.name}")
            try:
                _write(poster, data)
            except OSError as exc:
                raise MetaImportError(f"Обложка не записалась в папку: {exc}") from exc
            if current_poster is not None and current_poster != poster and (ours or replace_poster):
                if ours:
                    current_poster.unlink(missing_ok=True)     # the poster this import wrote before
            meta["poster_file"] = poster.name
            meta["poster_from"] = where
            meta["poster_md5"] = hashlib.md5(data).hexdigest()
            poster_note = poster.name
            say(f"Обложка: {poster.name} ({len(data) // 1024} КБ, {where})")
        else:
            say("Обложку взять не удалось: ни одна картинка страницы не открылась")
    elif current_poster is not None and not ours:
        say(f"Своя обложка {current_poster.name} оставлена (заменить — «заменить обложку»)")
    elif not candidates:
        say("Обложки на странице не нашлось" + (f" — осталась прежняя {current_poster.name}" if current_poster else ""))
    if "poster_file" not in meta and ours:
        for key in ("poster_file", "poster_from", "poster_md5"):   # the poster written before stays this import's
            if old.get(key):
                meta[key] = old[key]
    meta["imported_at"] = int(time.time())
    if page.origin:
        meta["imported_from"] = page.origin
    with INFO_LOCK:                        # the person's own keys as they are now (a mark given meanwhile)
        latest = _current_info(target, lambda _text: None) if target.exists() else old
        keep = {k: v for k, v in latest.items() if k not in meta and k not in video_meta.FIELD_LABELS
                and k not in ("title", "alt_titles", "extra", "tags", "ratings", "files", "source_url", "site", "ids",
                              "poster_file", "poster_from", "poster_md5", "imported_at", "format")}
        meta.update(keep)
        try:
            _write(target, json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError as exc:
            raise MetaImportError(f"Описание не записалось в папку: {exc}") from exc
    say(f"Записано: {target.name}")
    video_library.LIBRARY.invalidate()
    return {"ok": True, "title": meta.get("title") or "", "poster": poster_note, "fields": len(fields)}
