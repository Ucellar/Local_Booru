"""(v5.44.73) The pages of com-x.life as «Скачать манхву» reads them (``core/manhwa_sources.ComX`` asks for them).

com-x.life is a site of DataLife Engine (the address of a title — «/<number>-<the name in Latin letters>.html»).  What
its pages carry, as the readers of comics that read it say (it lets in only browsers — the program's own opens it):

* the title's page — ``window.__DATA__ = {…}`` with ``news_id`` (the number of the title) and ``chapters`` — [{id,
  title («Глава 12», «#12», «Том 1 Глава 12 - Имя»…), date («дд.мм.гггг»), posi — its place}]; the names in ``<h1>`` and
  ``og:title`` (« / » between them), the description, the poster ``og:image``, the facts «Тип: …», «Год: …»;
* a chapter's reader — ``/reader/<news_id>/<chapter id>``: its ``window.__DATA__`` ``images`` — paths of the pictures on
  ``img.com-x.life/comix/`` (or whole addresses);
* the search — ``/index.php?do=search`` (``story=``): links «/<number>-<name>.html» with the names.

Every reading here is a function of the page's text — the site's own changes are met here alone.
"""
from __future__ import annotations

import html as _html
import json
import re
from urllib.parse import urljoin

TYPES = {"манхва": "Манхва", "маньхуа": "Маньхуа", "манга": "Манга", "вебтун": "Манхва", "западный комикс": "Комикс",
         "комикс": "Комикс", "рукомикс": "Рукомикс"}
CLOSED_WORDS = re.compile(r"(?i)купить|подписк|премиум|доступ[а-я]* только")


def text_of(fragment) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", str(fragment or "")))).strip()


def data_of(page: str) -> dict:
    """The page's ``window.__DATA__`` ({} — none)."""
    m = re.search(r"window\.__DATA__\s*=\s*", page or "")
    if not m:
        return {}
    try:
        value, _end = json.JSONDecoder().raw_decode(page, m.end())
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def meta(page: str, prop: str) -> str:
    m = re.search(r'<meta[^>]+(?:property|name)="' + re.escape(prop) + r'"[^>]*content="([^"]*)"', page or "", re.I) or \
        re.search(r'<meta[^>]+content="([^"]*)"[^>]*(?:property|name)="' + re.escape(prop) + r'"', page or "", re.I)
    return _html.unescape(m.group(1)).strip() if m else ""


def _split(text: str) -> list:
    text = re.split(r"\s+[»|]\s+|\s+—\s+читать", text or "")[0]          # «Король меча » Читать комиксы онлайн»
    return [x.strip() for x in re.split(r"\s+/\s+", text) if x.strip()]


def names_of(page: str) -> list:
    """The title's names: its heading, the «original» name next to it, its ``og:title`` — « / » between names."""
    out: list = []
    found = [m.group(1) for m in re.finditer(r"<h1\b[^>]*>(.*?)</h1>", page or "", re.S | re.I)][:1]
    found += [m.group(1) for m in re.finditer(r'<[a-z0-9]+\b[^>]*class="[^"]*(?:original|title-en|alt-?name)[^"]*"[^>]*>'
                                              r'(.*?)</[a-z0-9]+>', page or "", re.S | re.I)][:2]
    found.append(meta(page, "og:title"))
    for raw in found:
        for name in _split(text_of(raw)):
            if name and name.casefold() not in {x.casefold() for x in out}:
                out.append(name)
    return out


def _block(page: str, classes: tuple) -> str:
    for cls in classes:
        m = re.search(r'<(div|section|article)\b[^>]*class="[^"]*\b' + re.escape(cls) + r'\b[^"]*"[^>]*>(.*?)</\1>',
                      page or "", re.S | re.I)
        if m and len(text_of(m.group(2))) >= 20:
            return m.group(2)
    return ""


def description_of(page: str) -> str:
    from core.manhwa_sources import plain
    block = _block(page, ("page__text", "full-text", "fdesc", "full_story", "description", "story"))
    return plain(block) if block else meta(page, "og:description") or meta(page, "description")


def lines_of(page: str) -> list:
    """The page's text line by line (the facts «Поле: значение»)."""
    text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", page or "", flags=re.S | re.I)
    text = re.sub(r"(?i)<br\s*/?>|</(?:li|p|div|tr|dd|dt|h\d)>", "\n", text)
    return [line for line in (text_of(x) for x in text.split("\n")) if line]


def facts_of(page: str) -> dict:
    """{"type", "status", "year", "genres"} from the facts of the page («Тип: Манхва», «Год выпуска: 2019»…)."""
    out: dict = {"type": "", "status": "", "year": 0, "genres": []}
    for line in lines_of(page):
        m = re.match(r"(?i)(тип|формат|статус|год(?: выпуска)?|жанры?|теги)\s*:\s*(.{1,200})$", line)
        if not m:
            continue
        field, value = m.group(1).casefold(), m.group(2).strip()
        if field in ("тип", "формат") and not out["type"]:
            out["type"] = next((name for key, name in TYPES.items() if key in value.casefold()), "")
        elif field == "статус" and not out["status"]:
            out["status"] = value[:60]
        elif field.startswith("год") and not out["year"]:
            year = re.search(r"(?:19|20)\d\d", value)
            out["year"] = int(year.group(0)) if year else 0
        elif field in ("жанр", "жанры", "теги") and not out["genres"]:
            out["genres"] = [g.strip() for g in value.split(",") if g.strip() and len(g.strip()) < 40][:20]
    return out


def numbers_of(title: str, posi=None) -> tuple:
    """(tome, number, the chapter's own name) of a chapter as the site writes it: «4 - 185 Конец 4-го сезона» (its
    list: the volume, a dash, the chapter, its name), «Том 1 Глава 12 - Имя», «#12», «185 Имя», «Король меча 12».

    (v5.44.145) Until then the last number of the line was the chapter: «4 - 185 Конец 4-го сезона» was chapter 4 —
    the site «had no» chapter 185 (the owner, 10.11.2026: «на com-x есть 185 глава»), and its pictures stood under
    the number 4.  A number inside the name is never the chapter's now: the site's own form first, then a word that
    names the number, then a number the line begins or ends with, then the chapter's place in the list.  The volume
    of the site's list («4 - …») is not taken, as before: the folders of the titles downloaded from it have none, and
    two ways of counting volumes in one folder would put its chapters out of order."""
    from core.manhwa_sources import _num
    title = text_of(title)
    named = re.search(r"(?i)(?:глава|chapter|\bch\.?|#|выпуск|эпизод)\s*(\d+(?:[.,]\d+)?)", title)
    listed = None if named else re.match(r"\s*(\d+)\s*[-–—]\s*(\d+(?:[.,]\d+)?)(?![\d.,]*\d)(.*)$", title, re.S)
    if listed:
        own = re.sub(r"^[\s:.\-–—]+", "", listed.group(3)).strip()
        return 0, _num(listed.group(2)), own
    tome = re.search(r"(?i)(?:\bтом|\bvol(?:ume)?\.?)\s*(\d+)", title)
    if named:
        number, rest = _num(named.group(1)), title[named.end():]
    else:
        after = title[tome.end():] if tome else title
        first = re.match(r"[\s:.\-–—]*(\d+(?:[.,]\d+)?)(?![\d.,]*\d)(.*)$", after, re.S)
        last = re.search(r"(\d+(?:[.,]\d+)?)\s*$", after)
        if first:                                   # «185 Конец 4-го сезона»
            number, rest = _num(first.group(1)), first.group(2)
        elif last:                                  # «Король меча 12»
            number, rest = _num(last.group(1)), ""
        else:                                       # no number of its own («Экстра», «Конец 4-го сезона»)
            number, rest = (_num(posi) if posi not in (None, "") else ""), ""
    own = re.sub(r"^[\s:.\-–—]+", "", rest).strip()
    return (int(tome.group(1)) if tome else 0), number, own


def picture_urls(page: str, data: dict, chapter_url: str, images_base: str) -> list:
    """The addresses of a reader's pictures: its ``images`` on the picture server (``images_base`` unless the page names
    its own), else the pictures of the reader in its HTML."""
    base = images_base.rstrip("/")
    for key in ("host", "img_host", "images_host", "imgHost", "server", "cdn"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://", "//")):
            base = value.rstrip("/")
            break
    out = []
    for item in data.get("images") or []:
        path = item if isinstance(item, str) else (item.get("src") or item.get("url") or item.get("path") or "") \
            if isinstance(item, dict) else ""
        path = str(path or "").strip().replace("\\/", "/")
        if not path:
            continue
        if path.startswith(("http://", "https://")):
            out.append(path)
        elif path.startswith("//"):
            out.append("https:" + path)
        else:
            tail = base.rsplit("/", 1)[-1]
            path = path.lstrip("/")
            out.append((base.rsplit("/", 1)[0] if tail and path.startswith(tail + "/") else base) + "/" + path)
    if not out:
        for src in re.findall(r'<img\b[^>]*?(?:data-src|src)="([^"]+)"', page or "", re.I):
            if "/comix/" in src or "img." in src:
                out.append(urljoin(chapter_url, _html.unescape(src)))
    return list(dict.fromkeys(out))


def chapters_in_html(page: str, news_id: str) -> list:
    """The chapters as links of the page («/reader/<news_id>/<id>» with their titles) — when ``__DATA__`` has none."""
    found: dict = {}
    for m in re.finditer(r'<a\b[^>]*href="(?:(?:https?:)?//[^/"]+)?/reader/' + re.escape(str(news_id)) +
                         r'/(\d+)/?"[^>]*>(.*?)</a>', page or "", re.S | re.I):
        title = text_of(m.group(2))
        if title and m.group(1) not in found:
            found[m.group(1)] = {"id": int(m.group(1)), "title": title}
    return list(found.values())


def keep(kind: str, page: str) -> str:
    """The last page of a kind («title», «reader», «search») the program read there, kept in the temporary folder of
    the computer (not in the archive) — when com-x changes its pages, what it gave can be looked at.  Its path, or
    "" when it is not written."""
    import logging
    import tempfile
    from pathlib import Path
    try:
        folder = Path(tempfile.gettempdir()) / "local_booru_comx"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{re.sub(r'[^a-z]', '', kind) or 'page'}.html"
        path.write_text(page or "", encoding="utf-8")
        return str(path)
    except OSError as exc:
        logging.getLogger("local_booru.manhwa_get").debug("com-x page not kept: %s", exc)
        return ""


def hits_of(page: str) -> list:
    """[(key «9504-latna-saga…», [names])] of a search page: its links of titles, in its order."""
    at = (page or "").find('id="dle-content"')
    body = page[at:] if at >= 0 else (page or "")
    found: dict = {}
    for m in re.finditer(r'<a\b[^>]*href="(?:(?:https?:)?//[^/"]+)?/(\d+)-([a-z0-9][a-z0-9-]*)\.html"[^>]*>(.*?)</a>',
                         body, re.S | re.I):
        key = f"{m.group(1)}-{m.group(2).lower()}"
        names = found.setdefault(key, [])
        inner = m.group(3)
        alt = re.search(r'\balt="([^"]+)"', inner)
        for name in _split(text_of(inner) or (_html.unescape(alt.group(1)) if alt else "")):
            if name not in names:
                names.append(name)
    return [(key, names) for key, names in found.items() if names]
