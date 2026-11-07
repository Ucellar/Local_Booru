"""(v5.44.76) «Скачать → Из архива»: the titles of the manga and manhwa folders that the program did not download itself
— known by their names on the sites of manga, linked to the title found, given its cover and its description with tags;
their missing chapters downloaded by «Докачать».

The owner (5.11.2026), of his friend's reader: «доработать парсер что бы он определял мангу в архиве и искал её и
выкачивал обложку и описание с тегами и докачивал недостающие».

* What is looked at (:func:`entries`): every top-level title of the manga folder (``core/manga_library.py``) and of the
  manhwa folder (``core/manhwa_library.py``) — a folder; a single archive at the top of the manga folder (one book) is
  only counted (there is no folder to keep its site in), the galleries of nhentai are not taken (their site is known).
  A folder whose ``.lb/source.json`` names a site is linked already (downloaded by the program, or linked here).
* Its names (:func:`names_for`): the title of its description (``info.json``/``meta.json``: ``title``, ``alt_titles``)
  and its folder's name without what is not the name (:func:`clean_name`: «[группа]», «(2019)», «Vol 1-10», «Том 1»,
  «complete», «_» …).
* The search (``core/manhwa_search.py`` ``search_now``): the sites at once; the answer the program takes by itself
  (:func:`decide`) — the one title that has one of its names and is of its kind (the manga folder — manga; the manhwa
  folder — the strips); several such, or none with its name — the owner chooses among the covers; nothing — «не
  нашлось», another name can be tried.
* Linking (:func:`link`): ``.lb/source.json`` in its folder (``archive`` — the mark of a title of the archive:
  ``follow`` off until the owner downloads), then the title looked at as by its link (``manhwa_download.look`` finds this
  folder by it — ``linked_folder``), its description written where the folder's own says nothing (``info.json``: what is
  there stays) and its cover where there is none; what the folder has of its chapters (``manhwa_download.had`` — chapter
  folders, .zip/.cbz, whole tomes) against what the sites have: «есть 40 из 380, не хватает 340».
* What was found is kept in ``manga_archive.json`` in the settings folder (:class:`Store`) — the candidates to choose
  from, «не нашлось», the counts.

(v5.44.81) The owner (5.11.2026): «у меня часто манга и манхва без названия или с названием но мне надо что бы парсер
сам находил и не парсил такое»; of the ways — «нужен не один вариант а несколько если не сработает»; «в фоне»:

* :func:`recognize` — the ways one after another (:class:`_Chain`), the next one only when this one is not sure:
  1. a link of a site the program knows in the folder's own files (``core/manga_clues.py``: shortcuts, descriptions,
     ComicInfo.xml; a chapter of MangaDex — its title asked) — linked to it;
  2. the names in its files (ComicInfo.xml, info.json, details.json, the name its chapter files share), then its
     folder's name — never a name that is no title's (``manga_clues.junk_name``: «Новая папка», «manga», «123»);
  3. its pictures — SauceNAO (``core/manga_pictures.py``: the cover, the first pages), when no site had a title of
     those names; its names (and MangaDex's title of its link) searched on the sites;
  4. «не нашлось» — with what was tried (``tried``), or the covers to choose from when something alike was found.
  How it was known is kept (``how``: link, names, folder, pictures, query, chosen, url; ``by`` — where, in words) and
  the page says it.
* :func:`link` keeps what it put into the folder (``added`` of ``.lb/source.json``: ``info.json`` made or changed —
  the owner's own kept as ``.lb/info.before.json``; the cover written): «Отвязать» (:func:`unlink_folder`) takes it
  back.  The chapters downloaded by «Докачать» stay.
* :func:`background_round` (``core/background_duties.py``, «Узнавать само» on the page — ``manga_archive_auto``): a
  few titles every ten minutes — the new ones first, then the ones whose pictures SauceNAO put off (when that time
  came), then «не нашлось» again after a week (the pictures again only when the folder changed); never the ones to
  choose from (the owner's), not while a download of manga or manhwa goes; SauceNAO not while the parser runs (they
  share its searches).  A round is a page task like the page's own — «Узнать», «Привязать» stop it and go first.
"""
from __future__ import annotations

import json
import zipfile
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from core import manga_clues as C
from core import manhwa_sources as S

_LOG = logging.getLogger("local_booru.manhwa")

TASK = "manga_archive"                 # the page task (one at a time)
STATE_FILE = "manga_archive.json"
CANDIDATES = 8                         # covers kept to choose from
QUERIES = 3                            # names a title is searched by at most
PICTURE_NAMES = 2                      # (v5.44.81) names of SauceNAO's answers searched by at most
KINDS = ("manga", "manhwa")
ROUND = 4                              # (v5.44.81) titles a round of the background takes
ROUND_SECONDS = 240                    # …and no new one after this long
AGAIN_DAYS = 7                         # «не нашлось» looked at again by the background after this many days
PARSER_LATER = 30 * 60                 # the pictures put off while the parser runs — asked again this much later
BACKGROUND = "background"              # the ``about`` of a page task of the background
BEFORE_FILE = "info.before.json"       # the owner's own info.json as it was before the link (in ``.lb``)

# what a folder's name carries besides the title
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|【[^】]*】|「[^」]*」")
_TAIL_WORDS = re.compile(
    r"(?:\b(?:vol(?:ume)?s?|v|т(?:ом)?а?|tomes?|ch(?:apter)?s?|c|гл(?:ава|авы)?|главы|часть|part|book|кн(?:ига)?)"
    r"\.?\s*\d+(?:[.,]\d+)?(?:\s*[-–—~]\s*\d+(?:[.,]\d+)?)?\b.*$)", re.I)
# words after the name only (a title may have «End» or «Manga» in it): «Berserk Complete», «Solo Leveling RAW»
_NOISE = re.compile(r"(?:(?:^|[\s\-–—])(?:complete[d]?|end|ongoing|raw|rus|eng|jpn?|scan(?:s|lation)?|digital|"
                    r"colou?red|webtoon|manga|манга|manhwa|манхва|manhua|маньхуа|завершен[оа]?|онгоинг|перевод|"
                    r"сканы?|cbz|zip))+\s*$", re.I)
_SEPARATORS = re.compile(r"[_.]+")


def clean_name(name: str) -> str:
    """A folder's name without the group, the year, the tomes and chapters it holds, «complete»…: «[Kirei] Berserk
    (1989) Vol 1-41 [Complete]» → «Berserk»."""
    text = re.sub(r"\.(?:zip|cbz|cbr|pdf)$", "", str(name or ""), flags=re.I)
    text = _BRACKETS.sub(" ", text)
    if not re.search(r"\s", text.strip()):          # «Some_Title_Vol_1» — the separators are the spaces
        text = _SEPARATORS.sub(" ", text)
    else:
        text = text.replace("_", " ")
    text = _TAIL_WORDS.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = re.sub(r"\s+[-–—:|]+\s*$", "", re.sub(r"\s+", " ", text)).strip(" -–—:|,.")
    return text


@dataclass
class Entry:
    """A title of the folders: ``key`` — «<kind>:<the library's id>»."""
    key: str
    kind: str                          # "manga" | "manhwa"
    lib_id: str
    path: str
    title: str
    names: list = field(default_factory=list)
    chapters: int = 0
    pages: int = 0
    linked: dict = field(default_factory=dict)          # its .lb/source.json, when it names a site
    folder: bool = True
    hentai: bool = False
    pages_of: list = field(default_factory=list)        # (v5.44.81) its cover and first pages (for SauceNAO)
    library: str = ""                  # (v5.44.84) "manga" | "manhwa" — the library it is in (its page there), or ""
    where: str = ""                    # (v5.44.84) its path in the parser's folder
    why: str = ""                      # (v5.44.84) why it is taken for manga (``core/manga_detect``)

    @property
    def sign(self) -> str:
        """(v5.44.81) Changes when the folder's chapters or pages do (the pictures asked again then)."""
        return f"{self.chapters}:{self.pages}"

    def public(self) -> dict:
        return {"key": self.key, "kind": self.kind, "id": self.lib_id, "title": self.title, "names": self.names[:4],
                "chapters": self.chapters, "pages": self.pages, "folder_name": Path(self.path).name,
                "linked": bool(self.linked), "archive": bool(self.linked.get("archive")),
                "site": self.linked.get("site", ""), "site_title": self.linked.get("title", ""),
                "url": self.linked.get("url", ""), "follow": bool(self.linked.get("follow")),
                "cover": f"/api/archive/folder-cover?k={self.key}", "where": self.where, "found_as": self.why,
                "page": (f"/manga/g/{self.lib_id}" if self.library == "manga" else
                         f"/manhwa/t/{self.lib_id}" if self.library == "manhwa" else "")}


def _info_of(folder: Path) -> dict:
    for name in ("info.json", "meta.json"):
        path = folder / name
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return data
    return {}


def names_for(title: str, folder: Path, info: dict | None = None) -> list:
    """The names a title is searched by: its description's, its library title, its folder's name made clean —
    (v5.44.81) none that is no title's (``manga_clues.junk_name``: «Новая папка (2)», «manga», «123», «Том 3»)."""
    info = _info_of(folder) if info is None else info
    return C.good_names([*C.names_in(info), clean_name(title), clean_name(folder.name)])


def _pictures(cover, pages: list) -> list:
    """(v5.44.81) What SauceNAO is asked of a title: its own cover, then the first page of its first chapter — or,
    with no cover, its first and third pages (the first is often a title page of the group)."""
    out = [cover] if cover is not None else []
    if pages:
        out.append(pages[0])
        if cover is None and len(pages) > 2:
            out.append(pages[2])
    return out[:3]


def _nhentai(manga) -> bool:
    return "nhentai" in str(getattr(manga, "source_url", "") or "").lower() or \
        Path(manga.path).name.lower().startswith("nhentai")


def _library_ids(settings: dict, *, refresh: bool = False) -> dict:
    """{folder (normcase'd): (kind, the library's id)} of the manga and manhwa folders — a title of the parser's folder
    that is in one of them has its page there."""
    from core import manga_library as M, manhwa_download as D, manhwa_library as H
    out = {}
    roots = D.roots(settings)
    if roots.get("manga") and Path(roots["manga"]).is_dir():
        for manga in M.LIBRARY.items(roots["manga"], refresh=refresh):
            out[os.path.normcase(os.path.abspath(manga.path))] = ("manga", manga.id)
    if roots.get("manhwa") and Path(roots["manhwa"]).is_dir():
        for title in H.LIBRARY.items(roots["manhwa"], refresh=refresh):
            out[os.path.normcase(os.path.abspath(title.path))] = ("manhwa", title.id)
    return out


def _kind_of(path: Path, settings: dict, first: list) -> str:
    """The kind of a title of the parser's folder: of the library it is in; else a strip («manhwa») when its pages are
    tall; else "" — not known before a site says it (``decide`` takes either)."""
    from core import manhwa_download as D
    folder = os.path.normcase(os.path.abspath(str(path)))
    for kind, root in D.roots(settings).items():
        if root and (folder + os.sep).startswith(os.path.normcase(os.path.abspath(root)) + os.sep):
            return kind
    sizes = []
    for item in first[:3]:
        try:
            from PIL import Image
            with Image.open(str(item)) as im:
                sizes.append(im.size)
        except (OSError, ValueError, TypeError):
            continue
    if sizes and all(w > 0 and h > 2.2 * w for w, h in sizes):
        return "manhwa"
    return ""


def _first_of(title) -> list:
    """The first pages of a title of the parser's folder (``manga_detect.Title.first``), or of its first archive."""
    from core.manhwa_library import Page
    pages = [Page(str(p)) for p in title.first if isinstance(p, str)]
    if pages:
        return pages
    path = Path(title.path)
    archives = [path] if path.is_file() else sorted(
        (x for x in path.iterdir() if x.suffix.lower() in (".cbz", ".zip")), key=lambda x: x.name.lower()) \
        if path.is_dir() else []
    for archive in archives[:1]:
        try:
            with zipfile.ZipFile(archive) as zf:
                names = sorted(n for n in zf.namelist() if Path(n).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
        except (OSError, zipfile.BadZipFile):
            continue
        pages += [Page(str(archive), member=n) for n in names[:3]]
    return pages


def entries(settings: dict, *, refresh: bool = False) -> list:
    """(v5.44.84) The titles of manga and manhwa in the booru parser's own folder (:class:`Entry`; ``core/manga_detect``:
    the last look at the whole folder — ``refresh``: looked at now) — the owner: «я говорил про архив что бы оно сверяло
    именно из архива который использует парсер booru а не из того в котором манга и манхва».  (v5.44.111) And the
    manga ``core/comic_manga`` put into the manga folder: the gallery's comics («comic:<key>») and the parser's titles
    it copied (their own key «root:<id>», the copy's folder)."""
    from core import comic_manga, manga_detect as MD, manhwa_download as D
    from core.manhwa_library import Page
    root = str(settings.get("root") or "").strip()
    if root and Path(root).is_dir() and refresh:
        MD.look(settings)
    library = _library_ids(settings, refresh=refresh)
    # (v5.44.111) the comics of the gallery and the manga of the parser's folder the program put into the manga folder
    # (``core/comic_manga``): looked up as those copies — a copied title of the parser's folder is not looked up twice
    out, copied = comic_manga.archive_entries(settings, library)
    if not root or not Path(root).is_dir():
        return sorted(out, key=lambda e: e.title.lower())
    for found in MD.STORE.titles(root):
        path = Path(found.path)
        if not path.exists() or _nhentai_folder(path) or f"root:{found.id}" in copied:
            continue
        folder = path.is_dir()
        linked = D.read_source(path) if folder and (path / D.LB_DIR / D.SOURCE_FILE).is_file() else {}
        info = _info_of(path) if folder else {}
        name = path.name if folder else path.stem
        title = str(info.get("title") or "").strip() or clean_name(name) or name
        lib = library.get(os.path.normcase(os.path.abspath(str(path))))
        first = _first_of(found)
        cover = next((path / f"cover{ext}" for ext in (".jpg", ".jpeg", ".png", ".webp")
                      if folder and (path / f"cover{ext}").is_file()), None)
        kind = (linked.get("kind") if linked.get("kind") in KINDS else "") or _kind_of(path, settings, found.first)
        out.append(Entry(key=f"root:{found.id}", kind=kind, lib_id=lib[1] if lib else found.id, path=str(path),
                         title=title, names=names_for(title, path, info) if folder else C.good_names([clean_name(name)]),
                         chapters=max(1, found.chapters), pages=found.pages, linked=linked if linked.get("site") else {},
                         folder=folder, hentai=_hentai(info, linked),
                         pages_of=_pictures(Page(str(cover)) if cover else None, first),
                         library=lib[0] if lib else "", where=_where(path, root), why=found.why))
    return sorted(out, key=lambda e: e.title.lower())


def first_page(settings: dict, key: str):
    """(v5.44.84) The picture a row of the page shows for a title of the parser's folder (its cover, else its first
    page) — of that title alone (the page asks one for each row)."""
    from core import manga_detect as MD
    from core.manhwa_library import Page
    if not str(key or "").startswith("root:"):
        return None
    found = next((t for t in MD.STORE.titles(str(settings.get("root") or "")) if t.id == key[5:]), None)
    if found is None:
        return None
    path = Path(found.path)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        if path.is_dir() and (path / f"cover{ext}").is_file():
            return Page(str(path / f"cover{ext}"))
    pages = _first_of(found)
    return pages[0] if pages else None


def _nhentai_folder(path: Path) -> bool:
    """A gallery of nhentai the program downloaded (its meta.json) — the manga's own, known."""
    if path.name.lower().startswith("nhentai"):
        return True
    meta = path / "meta.json"
    if path.is_dir() and meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and ("media_id" in data or "nhentai" in str(data.get("source") or "").lower())
    return False


def _hentai(info: dict, linked: dict) -> bool:
    """Its description says hentai (a genre «Хентай», the site's mark of erotica)."""
    if not isinstance(info, dict):
        return False
    genres = [str(g).lower() for g in (info.get("genres") or []) if isinstance(g, str)]
    return any(g in ("хентай", "hentai") for g in genres) or bool(info.get("is_erotic")) or bool(linked.get("erotic"))


def _where(path: Path, root: str) -> str:
    """Where a title is in the parser's folder: its path from there («2023 / Скачанное / Ван-Пис»)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return str(path)
    return " / ".join(rel.parts)


# ── what the program takes by itself ──────────────────────────────────────────────────────────────────────────────
def _tile_kind(tile) -> str:
    from core import manhwa_download as D
    kind = (tile.type or "").strip().lower()
    if not kind:
        return ""
    return "manga" if kind in D.MANGA_TYPES else "manhwa"


def decide(entry: Entry, tiles: list) -> tuple:
    """("sure", tile) — the one title that has one of the folder's names and is of its kind; ("choose", tiles) — the
    covers to choose from; ("none", []) — nothing found."""
    if not tiles:
        return "none", []
    mine = S.names_of(*entry.names)
    named = [t for t in tiles if mine & S.names_of(*t.names)]
    fitting = [t for t in named if not entry.kind or _tile_kind(t) in ("", entry.kind)]
    if len(fitting) == 1:
        return "sure", fitting[0]
    first = fitting or named
    return "choose", (first + [t for t in tiles if t not in first])[:CANDIDATES]


def candidate(tile) -> dict:
    """A tile as it is kept and shown: its names, kind, year, chapters, the sites and the cover's address."""
    from core import manhwa_search as MS
    labels = {s.name: s.label for s in S.SOURCES}
    public = tile.public(labels, "")
    cover, page = MS.best_cover(tile)
    public.update(cover_address=cover, cover_page=page)
    public.pop("cover", None)
    public.pop("exact", None)
    return public


# ── what was found, kept ────────────────────────────────────────────────────────────────────────────────────────────
class Store:
    """``manga_archive.json`` in the settings folder: {key: {state, query, candidates, at, counts, why, path}}."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._lock = threading.Lock()

    def file(self) -> Path:
        if self._path is not None:
            return self._path
        from core.paths import SETTINGS_DIR
        return Path(SETTINGS_DIR) / STATE_FILE

    def read(self) -> dict:
        from core.file_safety import read_json_kept
        data = read_json_kept(self.file(), dict, what="manga archive")
        return data if isinstance(data, dict) else {}

    def put(self, key: str, value: dict | None) -> None:
        from core.file_safety import write_json_kept
        with self._lock:
            data = self.read()
            if value is None:
                data.pop(key, None)
            else:
                data[key] = {**value, "at": int(time.time())}
            write_json_kept(self.file(), json.dumps(data, ensure_ascii=False, indent=1), dict)


STORE = Store()


# ── linking ─────────────────────────────────────────────────────────────────────────────────────────────────────────
class LinkError(RuntimeError):
    """Why a title was not linked, in words."""


def counts(found) -> dict:
    """What the folder has of the title's chapters and what the sites give: have, total, missing (free somewhere,
    not in the folder), waiting (paid everywhere)."""
    return {"have": len([c for c in found.chapters if found.is_had(c)]), "total": len(found.chapters),
            "missing": len(found.new(True)), "waiting": len(found.waiting(True))}


def _covers(folder: Path) -> list:
    try:
        return sorted(p.name for p in folder.glob("cover.*") if p.is_file())
    except OSError:
        return []


def _before(folder: Path) -> dict:
    """(v5.44.81) What the folder has before the program writes the title's description and cover into it: the
    owner's own ``info.json`` is kept as ``.lb/info.before.json`` (once — a link made again keeps the first)."""
    from core import manhwa_download as D
    info, kept = folder / "info.json", folder / D.LB_DIR / BEFORE_FILE
    if info.is_file() and not kept.exists():
        try:
            shutil.copy2(info, kept)
        except OSError as exc:
            _LOG.warning("the archive: %s not kept before the link: %s", info, exc)
    return {"info": info.is_file(), "covers": _covers(folder)}


def _added(folder: Path, before: dict, stored: dict) -> dict:
    """(v5.44.81) What the link put into the folder: ``info`` — "created" (there was none) or "changed" (the owner's,
    kept in ``.lb``), ``cover`` — the cover file it wrote ("" — none); an earlier link's are kept."""
    old = stored.get("added") if isinstance(stored.get("added"), dict) else {}
    fresh = [name for name in _covers(folder) if name not in before.get("covers", [])]
    return {"info": old.get("info") or ("changed" if before.get("info") else "created"),
            "cover": old.get("cover") or (fresh[0] if fresh else "")}


def link(settings: dict, entry: Entry, url: str, *, net=None, say=None) -> dict:
    """Link the folder of ``entry`` to the title at ``url``: ``.lb/source.json``, its description where the folder's own
    says nothing, its cover where there is none — (v5.44.81) what it put there kept in ``added`` («Отвязать» takes it
    back); {"title", "site", "url", "counts"} — or :class:`LinkError`."""
    from core import manhwa_download as D
    say = say or (lambda _text: None)
    folder = Path(entry.path)
    if not entry.folder or not folder.is_dir():
        raise LinkError("Это отдельный файл, а не папка: положите его в папку тайтла — тогда программа его узнает")
    source, key = S.source_for(url)
    if source is None:
        raise LinkError(D.UNKNOWN_LINK)
    net = net or S.Net()
    try:
        title = source.title(net, key)
    except S.SourceError as exc:
        raise LinkError(f"{source.label}: {exc}") from exc
    other = D.linked_folder(settings, title.site, title.key)
    if other is not None and Path(other[1]) != folder:
        raise LinkError(f"«{title.title}» с {source.label} уже привязан к папке «{Path(other[1]).name}»")
    stored = D.read_source(folder) if (folder / D.LB_DIR / D.SOURCE_FILE).is_file() else {}
    if stored.get("site") and not stored.get("archive") and (stored.get("site"), stored.get("key")) != (
            title.site, title.key):
        raise LinkError("Эту папку программа скачала сама с другого тайтла — её привязку не меняю")
    first = {**stored, "site": title.site, "key": title.key, "url": title.url, "title": title.title,
             "kind": entry.kind or D.kind_of(title), "archive": True, "follow": bool(stored.get("follow", False)),
             "linked_at": stored.get("linked_at") or int(time.time())}
    D.write_source(folder, first)                    # look() finds this folder by it
    try:
        found = D.look(title.url, settings, net=net, where=first["kind"], say=say)
    except D.DownloadError as exc:
        _forget(folder, stored)
        raise LinkError(str(exc)) from exc
    if Path(found.folder) != folder:
        _forget(folder, stored)
        raise LinkError("Программа не нашла эту папку по её привязке — попробуйте ещё раз")
    before = _before(folder)
    try:
        D._write_title(net, source, found, folder)
    except OSError as exc:
        _forget(folder, stored)
        raise LinkError(f"описание и обложка не записались в папку: {exc}") from exc
    record = {**D.read_source(folder), "branch": found.branch.id if found.branch else "",
              "branch_name": found.branch.name if found.branch else "", **found.helper_record,
              "helpers_on": found.helpers_on, "licensed": found.title.licensed,
              "waiting": [{"id": c.id, "label": c.label, "free_at": c.free_at} for c in found.waiting(True)],
              "added": _added(folder, before, stored)}
    D.write_source(folder, record)
    numbers = counts(found)
    say(f"«{entry.title}» → {source.label}: «{found.title.title}» — в папке {numbers['have']} из {numbers['total']} "
        f"глав" + (f", не хватает {numbers['missing']}" if numbers["missing"] else ""))
    return {"title": found.title.title, "site": source.label, "url": found.title.url, "counts": numbers}


def _forget(folder: Path, stored: dict) -> None:
    """The link written for a look that did not come: back as it was."""
    from core import manhwa_download as D
    if stored:
        D.write_source(folder, stored)
        return
    unlink_folder(folder)


def _take_back(folder: Path, added) -> None:
    """(v5.44.81) What the link put into the folder, taken back: the ``info.json`` it made — removed, the owner's — his
    own again (``.lb/info.before.json``); the cover it wrote — removed."""
    from core import manhwa_download as D
    if not isinstance(added, dict):
        return
    info, kept = folder / "info.json", folder / D.LB_DIR / BEFORE_FILE
    try:
        if added.get("info") == "created" and info.is_file():
            info.unlink()
        elif added.get("info") == "changed" and kept.is_file():
            os.replace(kept, info)
    except OSError as exc:
        _LOG.warning("the archive: the description of %s not taken back: %s", folder, exc)
    cover = str(added.get("cover") or "")
    if re.fullmatch(r"cover\.[A-Za-z0-9]{2,5}", cover):
        try:
            (folder / cover).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _LOG.warning("the archive: the cover of %s not taken back: %s", folder, exc)


def unlink_folder(folder: Path) -> bool:
    """The link of a title of the archive taken off (its ``.lb/source.json``) — (v5.44.81) with what the link put into
    the folder (the description, the cover: :func:`_take_back`; the chapters «Докачать» downloaded stay).  False — the
    folder is not one the program linked here (it downloaded it itself)."""
    from core import manhwa_download as D
    folder = Path(folder)
    path = folder / D.LB_DIR / D.SOURCE_FILE
    if not path.is_file():
        return True
    source = D.read_source(folder)
    if not source.get("archive"):
        return False
    _take_back(folder, source.get("added"))
    for name in (D.SOURCE_FILE, D.SOURCE_FILE + ".bak", D.SOURCE_FILE + ".tmp", D.SOURCE_FILE + ".bak.tmp",
                 BEFORE_FILE):
        try:
            (folder / D.LB_DIR / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _LOG.warning("archive link of %s not taken off: %s", folder, exc)
            return False
    try:
        (folder / D.LB_DIR).rmdir()                  # only when nothing else of the program is there
    except OSError:
        pass
    for library in ("manga_library", "manhwa_library"):  # the cover and the title the libraries keep — read anew
        try:
            module = __import__(f"core.{library}", fromlist=["LIBRARY"])
            module.LIBRARY.invalidate()
        except (ImportError, AttributeError):
            pass
    return True


# ── (v5.44.81) the ways, one after another ──────────────────────────────────────────────────────────────────────────
WAYS = {"link": "по ссылке в файлах папки", "names": "по названию из файлов папки", "folder": "по имени папки",
        "pictures": "по картинкам (SauceNAO)", "query": "по вашему названию", "chosen": "выбран вами",
        "url": "по вашей ссылке"}


def _named(entry: Entry, tiles: list) -> bool:
    """Some tile carries one of the title's names (several such — the owner chooses; none — the names found only
    something else)."""
    mine = S.names_of(*entry.names)
    return bool(mine) and any(mine & S.names_of(*t.names) for t in tiles)


def _shown(entry: Entry, tiles: list) -> list:
    """The covers kept to choose from, the ones with the title's names first."""
    verdict, picked = decide(entry, tiles)
    ordered = [picked, *[t for t in tiles if t is not picked]] if verdict == "sure" else picked
    return [candidate(t) for t in ordered[:CANDIDATES]]


def _with_title(tiles: list, title) -> list:
    """The tiles of a search with a title of one site added (MangaDex's of SauceNAO's link) — into the tile of the
    same title when the search has one, else as a tile of its own at the end."""
    from core import manhwa_search as MS
    order = next((n for n, s in enumerate(S.SOURCES) if s.name == title.site), len(S.SOURCES))
    names = [n for n in [title.title, *title.alt_titles] if n]
    hit = S.Hit(site=title.site, key=title.key, names=names, type=title.type, year=title.year, url=title.url,
                cover=title.cover_url)
    if any(h.site == title.site and h.key == title.key for tile in tiles for _o, _r, h in tile.hits):
        return tiles
    for tile in tiles:
        if any(h.site == title.site for _o, _r, h in tile.hits):
            continue
        if S.names_of(*names) & S.names_of(*tile.names) and S._fits(title.type, title.year, tile.type, tile.year):
            tile.hits.append((order, 0, hit))
            tile.names += [n for n in names if n not in tile.names]
            return tiles
    return [*tiles, MS.Tile(names=list(names), type=title.type, year=title.year, hits=[(order, 0, hit)])]


class _Chain:
    """One title of the folders found by the ways one after another (the module's words).  :meth:`run` — the record
    :data:`STORE` keeps of it; None — stopped half-way (nothing kept)."""

    def __init__(self, settings: dict, entry: Entry, *, net, say, stop_check, pictures_off: str = "",
                 postpone=None):
        self.settings, self.entry, self.net, self.say, self.stop = settings, entry, net, say, stop_check
        self.pictures_off = pictures_off            # why the pictures are not asked this time ("" — they may be)
        self.postpone = postpone                    # () -> why they are put off now ("" — not)
        self.tried: list = []
        self.failed: dict = {}
        self.names: list = list(entry.names)
        self.pictures_sign = ""
        self.clue = C.Clues()

    def record(self, state: str, **values) -> dict:
        out = {"state": state, "path": self.entry.path, "query": "", "candidates": [], "how": "", "by": "",
               "tried": self.tried[:8], "why": "", "later_at": 0, "sign": self.entry.sign}
        if self.pictures_sign:
            out["pictures_sign"] = self.pictures_sign
        out.update(values)
        return out

    def linked(self, url: str, how: str, by: str, asked: str = "") -> dict:
        done = link(self.settings, self.entry, url, net=self.net, say=lambda text: self.say("  " + text))
        return self.record("linked", how=how, by=by, query=asked, counts=done["counts"])

    def sure(self, tile, tiles: list, probe: Entry, how: str, by: str, asked: str) -> dict:
        """The one title found linked; it cannot be (another folder has it…) — the covers to choose from, with why."""
        try:
            return self.linked(_best_url(tile), how, by, asked)
        except LinkError as exc:
            self.say(f"  не привязан: {exc}")
            return self.record("choose", query=asked, why=str(exc), candidates=_shown(probe, tiles), how=how, by=by)

    # the ways
    def run(self, query: str = "") -> "dict | None":
        if query:
            return self._query(query)
        got = self._links()
        if got is not None or self.stop():
            return got
        got, (tiles, asked, named) = self._names()
        if got is not None or self.stop():
            return got
        probe = replace(self.entry, names=self.names)
        later_at = 0
        if not named:                               # several titles of its name: the pictures would not tell them
            got, later_at = self._pictures()
            if got is not None or self.stop():
                return got
        if tiles and not later_at:
            return self.record("choose", query=asked, candidates=_shown(probe, tiles))
        return self.record("none", query=asked, later_at=later_at,
                           candidates=_shown(probe, tiles) if tiles else [], why=self._silent(tiles, asked))

    def _silent(self, tiles: list, asked: str) -> str:
        """«сайты не ответили» — when every site asked failed (some of them — said in ``tried``)."""
        from core import manhwa_search as MS
        return "сайты не ответили" if asked and not tiles and len(self.failed) >= max(1, len(MS.sources())) else ""

    def _query(self, query: str) -> "dict | None":
        """The owner's own name of it (one search)."""
        from core import manhwa_search as MS
        found, missed = MS.search_now(query, self.stop)
        if self.stop():
            return None
        probe = replace(self.entry, names=C.good_names([query, *self.entry.names]) or [query])
        verdict, picked = decide(probe, found)
        if verdict == "sure":
            return self.sure(picked, found, probe, "query", f"«{query}»", query)
        if verdict == "choose":
            return self.record("choose", query=query, candidates=[candidate(t) for t in picked])
        self.failed.update(missed)
        self.tried.append(f"ваше название «{query}» — на сайтах не нашлось")
        return self.record("none", query=query, why=self._silent(found, query))

    def _title_link(self, url: str) -> str:
        chapter = C.mangadex_chapter(url)
        if not chapter:
            return url
        mangadex = S.by_name("mangadex")
        return mangadex.title_of_chapter(self.net, chapter) if mangadex is not None and mangadex.readable else ""

    def _links(self) -> "dict | None":
        """1. A link of a site the program knows in the folder's own files."""
        self.clue = C.clues(Path(self.entry.path))
        if not self.clue.links:
            self.tried.append("ссылок на сайты в файлах папки нет")
            return None
        for url, where in self.clue.links:
            if self.stop():
                return None
            try:
                target = self._title_link(url)
            except S.SourceError as exc:
                self.tried.append(f"ссылка из {where}: {exc}")
                continue
            if not target:
                self.tried.append(f"ссылка из {where}: тайтла по ней нет")
                continue
            try:
                return self.linked(target, "link", where)
            except LinkError as exc:
                self.say(f"  ссылка из {where} не подошла: {exc}")
                self.tried.append(f"ссылка из {where}: {exc}")
        return None

    def _names(self) -> tuple:
        """2. The names of its files, then its folder's own (never one that is no title's) — (a record or None,
        (the tiles to show, the name they were found by, whether they carry its name))."""
        from core import manhwa_search as MS
        names, seen = [], set()
        for name, how, where in [(n, "names", w) for n, w in self.clue.names] + \
                [(n, "folder", "") for n in self.entry.names]:
            key = S.name_key(name)
            if key and key not in seen:
                seen.add(key)
                names.append((name, how, where))
        self.names = [n for n, _h, _w in names]
        probe = replace(self.entry, names=self.names)
        tiles, asked, named = [], "", False
        for name, how, where in names[:QUERIES]:
            if self.stop():
                return None, (tiles, asked, named)
            found, missed = MS.search_now(name, self.stop)
            self.failed.update(missed)
            verdict, picked = decide(probe, found)
            if verdict == "sure":
                by = f"«{name}»" + (f" из {where}" if how == "names" else "")
                return self.sure(picked, found, probe, how, by, name), (found, name, True)
            has = _named(probe, found)
            if found and (not tiles or has and not named):
                tiles, asked, named = found, name, has
        if not names:
            self.tried.append("названия нет ни в файлах папки, ни в её имени")
        else:
            listed = ", ".join(f"«{n}»" for n, _h, _w in names[:QUERIES])
            self.tried.append(f"по названию {listed}: " + (
                "похожих несколько" if named else "точно такого на сайтах нет" if tiles else "на сайтах не нашлось")
                + (" (не ответили: " + ", ".join(self.failed) + ")" if self.failed and not tiles else ""))
        return None, (tiles, asked or (names[0][0] if names else ""), named)

    def _mangadex(self, links: list):
        """MangaDex's title of SauceNAO's links (a chapter's or the title's); None — none."""
        mangadex = S.by_name("mangadex")
        if mangadex is None or not mangadex.readable:
            return None
        for url, _sim in links[:2]:
            try:
                target = self._title_link(url)
                key = mangadex.key_of(target) if target else ""
                if key:
                    return mangadex.title(self.net, key)
            except S.SourceError as exc:
                _LOG.info("the archive: MangaDex's title of %s not asked: %s", url, exc)
        return None

    def _pictures(self) -> tuple:
        """3. Its pictures — SauceNAO (``core/manga_pictures.py``): (a record or None, when to ask them again — 0)."""
        from core import manga_pictures as P, manhwa_search as MS
        if not self.entry.pages_of:
            self.tried.append("по картинкам — в папке нет картинок")
            return None, 0
        if self.pictures_off:
            self.tried.append(f"по картинкам — {self.pictures_off}")
            return None, 0
        wait = self.postpone() if self.postpone is not None else ""
        if wait:
            self.tried.append(f"по картинкам — позже: {wait}")
            return None, int(time.time()) + PARSER_LATER
        seen = P.look(self.settings, self.entry.pages_of, stop_check=self.stop, say=self.say)
        if seen.asked:
            self.pictures_sign = self.entry.sign
        if seen.off:
            self.tried.append(f"по картинкам — {seen.off}")
            return None, 0
        if seen.later and not seen.names and not seen.links:
            self.tried.append(f"по картинкам — позже: {seen.later}")
            return None, seen.later_at
        if not seen.names and not seen.links:
            self.tried.append("по картинкам — SauceNAO такую мангу не знает" if seen.asked else
                              "по картинкам — картинки папки не прочитались")
            return None, 0
        if seen.names:
            self.say("  по картинкам (SauceNAO): " + ", ".join(f"«{n}» — {s:.0f} %" for n, s in seen.names[:3]))
        title = self._mangadex(seen.links)
        names = C.good_names([*([title.title, *title.alt_titles] if title is not None else []),
                              *(n for n, _s in seen.names)])
        if not names:
            self.tried.append("по картинкам — SauceNAO назвал то, что не название")
            return None, 0
        probe = replace(self.entry, names=C.good_names([*self.names, *names]))
        by = f"«{seen.names[0][0] if seen.names else names[0]}», SauceNAO {seen.best:.0f} %"
        tiles, asked = [], ""
        for name in names[:PICTURE_NAMES]:
            if self.stop():
                return None, 0
            found, missed = MS.search_now(name, self.stop)
            self.failed.update(missed)
            if title is not None:
                found = _with_title(found, title)
            verdict, picked = decide(probe, found)
            if verdict == "sure" and seen.sure:
                return self.sure(picked, found, probe, "pictures", by, name), 0
            if found and not tiles:
                tiles, asked = found, name
        if not tiles and title is not None:
            tiles, asked = _with_title([], title), names[0]
            verdict, picked = decide(probe, tiles)
            if verdict == "sure" and seen.sure:
                return self.sure(picked, tiles, probe, "pictures", by, asked), 0
        if tiles:
            self.tried.append(f"по картинкам: {by} — " + ("не уверен, выберите" if not seen.sure else
                                                            "похожих несколько"))
            return self.record("choose", query=asked, candidates=_shown(probe, tiles), how="pictures", by=by), 0
        self.tried.append(f"по картинкам: {by} — на сайтах такого нет")
        return None, 0


def _best_url(tile) -> str:
    hits = sorted(tile.hits, key=lambda x: (x[0], x[1]))
    return hits[0][2].url if hits else ""


def _summary(tally: dict) -> str:
    return (f"Готово: привязано {tally['linked']}, выбрать {tally['choose']}, не нашлось {tally['none']}"
            + (f", не привязано {tally['failed']}" if tally["failed"] else ""))


# ── the work of the page ───────────────────────────────────────────────────────────────────────────────────────────
def recognize(settings: dict, chosen: list, *, say, stop_check, net=None, query: str = "", pictures_off="",
              postpone=None, enough=None) -> dict:
    """Each title of ``chosen`` (:class:`Entry`) found by the ways (:class:`_Chain`; ``query`` — by this name alone)
    and linked when the answer is sure; what was found kept in :data:`STORE`.  ``pictures_off`` — why SauceNAO is not
    asked this time (or (entry, its record) -> why), ``postpone`` — () -> why it is put off now, ``enough`` — () ->
    True when no further title is begun (the background's time).  The counts of each outcome."""
    net = net or S.Net(stop_check)
    tally = {"linked": 0, "choose": 0, "none": 0, "failed": 0}
    kept = STORE.read()
    for n, entry in enumerate(chosen, 1):
        if stop_check() or n > 1 and enough is not None and enough():
            break
        if not entry.folder:
            continue
        say(f"[{n}/{len(chosen)}] «{entry.title}»")
        old = kept.get(entry.key) or {}
        off = pictures_off(entry, old) if callable(pictures_off) else pictures_off
        chain = _Chain(settings, entry, net=net, say=say, stop_check=stop_check, pictures_off=off, postpone=postpone)
        record = chain.run(query)
        if record is None:                           # stopped half-way: nothing kept of it
            break
        if not record.get("pictures_sign") and old.get("pictures_sign"):
            record["pictures_sign"] = old["pictures_sign"]
        STORE.put(entry.key, record)
        state = record["state"]
        if state == "linked":
            tally["linked"] += 1
            say(f"  узнан {WAYS.get(record['how'], '')}" + (f": {record['by']}" if record.get("by") else ""))
        elif state == "choose":
            tally["failed" if record.get("why") else "choose"] += 1
            shown = record.get("candidates") or []
            say(f"  нужно выбрать: {len(shown)} — " + "; ".join(f"«{c.get('title') or '?'}»" for c in shown[:3]))
        else:
            tally["none"] += 1
            say("  не нашлось: " + "; ".join(record.get("tried") or []))
    return tally


def refresh_counts(settings: dict, entry: Entry, *, net=None, say=None) -> dict:
    """The counts of a linked title asked again (its chapters on the sites, what the folder has now)."""
    from core import manhwa_download as D
    found = D.look(entry.linked["url"], settings, net=net, where=entry.kind, say=say, about=False,
                   branch=str(entry.linked.get("branch") or ""))
    numbers = counts(found)
    STORE.put(entry.key, {**STORE.read().get(entry.key, {}), "state": "linked", "path": entry.path, "counts": numbers})
    return numbers


def note_download(folder: Path, found) -> None:
    """A download into a title of the archive has ended: its counts as the folder is now (``manhwa_download``)."""
    from core import manhwa_download as D
    found.have = D.had(Path(folder), found.title.site)
    kept = STORE.read()
    for key, value in kept.items():
        if isinstance(value, dict) and value.get("path") and Path(value["path"]) == Path(folder):
            STORE.put(key, {**value, "state": "linked", "counts": counts(found)})


def queue_missing(settings: dict, chosen: list, *, say) -> int:
    """The missing chapters of the linked titles of ``chosen`` — into the downloads' queue (``core/manhwa_queue.py``:
    one after another, the title found again by its link, every chapter it has not; new ones followed from then on)."""
    from core import manhwa_queue as Q
    queued = 0
    for entry in chosen:
        if not entry.linked or not entry.linked.get("url"):
            continue
        kept = STORE.read().get(entry.key, {})
        if (kept.get("counts") or {}).get("missing") == 0:
            continue
        record = {"url": entry.linked["url"], "title": entry.linked.get("title") or entry.title,
                  "branch": str(entry.linked.get("branch") or ""), "where": entry.kind, "follow": True,
                  "helpers": bool(entry.linked.get("helpers_on", True)), "chapters": None}
        label = f"Докачать: {record['title']}"

        def work(say_, stop_, record=record):
            return Q.work(record, settings, say_, stop_)
        Q.start(label, work, record=record, about=f"archive:{entry.key}", title=record["title"],
                count=int((kept.get("counts") or {}).get("missing") or 0))
        queued += 1
        say(f"В очередь скачивания: «{record['title']}»")
    return queued


def _give_way() -> None:
    """(v5.44.81) A round of the background gives way to the page's own work: stopped, waited for a little (it stops
    between the requests of a search)."""
    from core import page_jobs
    task = page_jobs.current(TASK)
    if task is not None and task.running and task.about == BACKGROUND:
        task.stop()
        task.wait(12.0)


def start(settings: dict, what: str, keys: list | None = None, *, query: str = "", url: str = "",
          how: str = "chosen") -> object:
    """The page's work as a page task: "recognize" (the titles of ``keys``, all not linked when None), "link" (one
    title to ``url``; ``how`` — "chosen" (a cover of the found) or "url" (the owner's link)), "counts" (the linked
    ones asked again), "download" (their missing chapters into the queue).  ``page_jobs.TaskBusy`` — one goes; (v5.44.81)
    a round of the background is stopped first."""
    from core import page_jobs
    settings = dict(settings)

    def chosen_of() -> list:
        # (v5.44.84) the parser's whole folder looked at again only for «Узнать неузнанные»; one title — as listed
        every = entries(settings, refresh=what == "recognize" and keys is None)
        if keys is None:
            if what == "recognize":                  # the ones not looked for yet (the others — one by one)
                kept = STORE.read()
                return [e for e in every if e.folder and not e.linked and
                        (kept.get(e.key) or {}).get("state") not in ("choose", "none")]
            return [e for e in every if e.linked]
        wanted = set(keys)
        return [e for e in every if e.key in wanted]

    def run(say, stop_check):
        if what == "scan":                           # (v5.44.84) «Проверить папку парсера»
            from core import manga_detect as MD
            found = MD.look(settings, stop_check=stop_check, say=say)
            return {"titles": len(found), "stopped": bool(stop_check())}
        chosen = chosen_of()
        if what == "link":
            if not chosen:
                return {"failed": "Этой папки больше нет — список устарел, откройте страницу заново"}
            entry = chosen[0]
            try:
                done = link(settings, entry, url, net=S.Net(stop_check), say=say)
            except LinkError as exc:
                return {"failed": str(exc)}
            STORE.put(entry.key, {**STORE.read().get(entry.key, {}), "state": "linked", "path": entry.path,
                                  "counts": done["counts"], "why": "", "how": how,
                                  "by": "" if how == "chosen" else url, "tried": [], "later_at": 0})
            return {"linked": 1, "title": done["title"]}
        if what == "counts":
            net = S.Net(stop_check)
            for entry in chosen:
                if stop_check():
                    break
                try:
                    numbers = refresh_counts(settings, entry, net=net)
                except Exception as exc:                  # noqa: BLE001 - one title's trouble: the others go on
                    say(f"«{entry.title}»: {exc}")
                    continue
                say(f"«{entry.title}»: в папке {numbers['have']} из {numbers['total']}, не хватает {numbers['missing']}")
            return {"counted": len(chosen), "stopped": bool(stop_check())}
        if what == "download":
            return {"queued": queue_missing(settings, chosen, say=say)}
        if not chosen:
            return {"recognized": 0, "note": "Все тайтлы папок уже узнаны"}
        say(f"Узнаю тайтлов: {len(chosen)}")
        tally = recognize(settings, chosen, say=say, stop_check=stop_check, query=query)
        say(_summary(tally))
        return {**tally, "stopped": bool(stop_check())}

    labels = {"recognize": "Узнать тайтлы", "link": "Привязать тайтл", "counts": "Сверить главы",
              "download": "Докачать недостающее", "scan": "Проверить папку парсера"}
    _give_way()
    return page_jobs.start(TASK, labels.get(what, what), run)


# ── (v5.44.81) by itself, in the background ─────────────────────────────────────────────────────────────────────────
_LOOKING = threading.Lock()


def _pictures_again(entry: Entry, record: dict) -> str:
    """The pictures of a title «не нашлось» looked at again: asked again only when its folder changed since."""
    if record.get("pictures_sign") and record.get("pictures_sign") == entry.sign and not record.get("later_at"):
        return "уже искали, папка с тех пор не менялась"
    return ""


def due(settings: dict, *, now: float | None = None) -> list:
    """The titles a round of the background takes (:data:`ROUND` at most): the ones never looked at first, then the
    ones whose pictures were put off (when that time has come), then «не нашлось» of :data:`AGAIN_DAYS` days ago;
    never a linked one nor one to choose from (the owner's)."""
    now = time.time() if now is None else now
    kept = STORE.read()
    fresh, later, again = [], [], []
    for entry in entries(settings):
        if not entry.folder or entry.linked:
            continue
        record = kept.get(entry.key) or {}
        state = record.get("state")
        if state == "choose":
            continue
        if state != "none":
            fresh.append(entry)                      # never looked at (or its link was taken off by hand)
        elif record.get("later_at"):
            if float(record["later_at"]) <= now:
                later.append(entry)
        elif now - float(record.get("at") or 0) >= AGAIN_DAYS * 86400:
            again.append(entry)
    return (fresh + later + again)[:ROUND]


def _parser_busy() -> str:
    from core.parser import jobs as parser_jobs
    return "идёт парсер — поиски SauceNAO нужны ему" if parser_jobs.is_running() else ""


def background_round(settings: dict, live=None) -> bool:
    """(``core/background_duties.py``, every ten minutes) A round of the titles :func:`due` as a page task of its own
    («Узнаю тайтлы в фоне»: the page shows it, «Остановить» stops it, the page's own work stops it first) — when
    «Узнавать само» is on, no work of the page goes and no download of manga or manhwa.  The folders are looked at in
    a thread of its own (a big library's first scan takes long): True — it began."""
    settings = dict(settings or {})
    if not settings.get("manga_archive_auto", True):
        return False
    from core import manhwa_download as D, manhwa_queue as Q, page_jobs
    task = page_jobs.current(TASK)
    if task is not None and task.running:
        return False
    download = page_jobs.current(D.TASK)
    if download is not None and download.running or Q.waiting():
        return False
    if not _LOOKING.acquire(blocking=False):
        return False
    live = live or (lambda: settings)

    def look() -> None:
        try:
            from core import manga_detect as MD
            root = str(settings.get("root") or "")
            if root and MD.STORE.due(root):          # (v5.44.84) the parser's folder looked at again (in this thread)
                MD.look(settings)
            chosen = due(settings)
            if not chosen:
                return
            deadline = time.monotonic() + ROUND_SECONDS

            def run(say, stop_check):
                say(f"В фоне: узнаю тайтлов — {len(chosen)}")
                tally = recognize(dict(live() or settings), chosen, say=say, stop_check=stop_check,
                                  pictures_off=_pictures_again, postpone=_parser_busy,
                                  enough=lambda: time.monotonic() > deadline)
                say(_summary(tally))
                return {**tally, "stopped": bool(stop_check()), "background": True}
            try:
                page_jobs.start(TASK, "Узнаю тайтлы в фоне", run, about=BACKGROUND)
            except page_jobs.TaskBusy:
                return
            _LOG.info("the archive: a round of the background began (%d title(s))", len(chosen))
        except Exception as exc:                      # noqa: BLE001 - the next round tries again
            _LOG.warning("the archive: the round of the background failed: %s: %s", type(exc).__name__, exc)
        finally:
            _LOOKING.release()
    threading.Thread(target=look, name="manga-archive-round", daemon=True).start()
    return True
