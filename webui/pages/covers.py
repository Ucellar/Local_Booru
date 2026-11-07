"""(v5.44.123) The menu of covers — step 7 of the plan, the owner's approved mockup F (7.11.2026: «сделать
возможнотсь выбора обложки манги и аниме (они разные у сайтов) нажал на обложку вылезла менюшка с выбором обложки»;
the mockup — «норм мне нравится»).  A click on the cover of a title's page (``webui/pages/title.py``) or of a series'
page (``video_series.py``) — the computer's and the phone's — opens it (``static/covers.js``, ``covers.css``): the
covers of the sites the title was found on, each with its site and size, the one now marked, «Своя картинка…»,
«Вернуть обложку сайта».  A click — the cover at once: into the title's folder (``cover.<ext>``; a series —
``poster.<ext>``), the one before kept beside; a title without a folder — kept by the program (``core/title_covers``).

* ``GET /api/covers?find=<the title page's search>`` — a title of manga or manhwa; ``?series=<id>`` / ``?url=`` — a
  series (its folder, its site);
* ``GET /api/covers/pic?…&k=`` — the picture of an option, whole (its size is the site's; ``server.MEDIA_PATHS``);
* ``POST /api/covers/choose`` {find | series | url, k | back} — the cover chosen;
* ``POST /api/covers/own?find=|series=|url=`` — the body is a picture of one's own («Своя картинка…»).

The covers of the sites are the addresses the sites gave of this title (``manhwa_download.Found.covers``, the series'
site's answer) — no site is asked for a cover it did not name; a picture comes through the program, kept a while.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from urllib.parse import quote, urljoin

from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")

PICTURE_TTL = 600.0                   # AniLiberty's poster's address of a release, kept this long
SITE_WAIT = 2.5                       # seconds a series' site is waited for (its page has asked for it already)
_RELEASES: dict = {}                  # AniLiberty's alias → (when, the poster's address)
_RELEASES_LOCK = threading.Lock()


def register(router) -> None:
    router.add("GET", r"/api/covers", listing)
    router.add("GET", r"/api/covers/pic", picture)
    router.add("POST", r"/api/covers/choose", choose)
    router.add("POST", r"/api/covers/own", own)
    from core import page_jobs
    page_jobs.on_end("video_download", _video_ended)     # a series chosen for without a folder: its folder now


def _key(text: str) -> str:
    return hashlib.md5(str(text or "").encode("utf-8")).hexdigest()[:12]


def _words_of(result: dict, stem: str, label: str) -> str:
    """«Обложка — с MangaLib; прежняя осталась в папке: cover.old-….jpg»."""
    said = f"Обложка — {label}"
    if result.get("same"):
        return said + " (она и стояла)"
    if result.get("back"):
        said += f" — она была в папке рядом ({result['back']}), стала {stem}"
    if result.get("aside"):
        said += "; прежняя осталась в папке: " + ", ".join(result["aside"])
    return said


# ── a title of manga or manhwa (the one page of a title, its search of «Найти») ──────────────────────────────────────
class _Title:
    """The cover of the title the page's search found (``manhwa_download.Found``)."""

    def __init__(self, req: Request, entry, found):
        from core import manhwa_download as D, title_covers as TC
        self.req, self.entry, self.found = req, entry, found
        self.folder = Path(found.folder)
        linked = self.folder.is_dir() and bool(D.read_source(self.folder).get("site")) \
            if (self.folder / D.LB_DIR / D.SOURCE_FILE).is_file() else False
        self.kind = TC.in_library(self.folder, req.settings) if linked else ""    # its folder is the program's
        self.ids = D.cover_ids(found)
        self.home = found.title.site
        self.stem = "cover"

    @property
    def name(self) -> str:
        info = self.found.about or self.found.title
        return info.title or self.found.title.title

    def options(self) -> list:
        from core import title_covers as TC
        record = TC.chosen(self.ids)
        options = [dict(c, kind="site", k="s-" + _key(c["url"])) for c in self.found.covers]
        now = None

        def site_of(entry: dict):
            return next((o for o in options if o["site"] == entry.get("site") and o["url"] == entry.get("url")),
                        None) or next((o for o in options if o["site"] == entry.get("site")), None)
        if self.kind:                                     # the folder's cover is the cover
            shown = TC.current(self.folder)
            digest = TC._md5_file(shown) if shown is not None else ""
            if shown is not None and record and record.get("md5") == digest and record.get("how") == "site":
                now = site_of(record)
            if now is None and shown is not None:
                mine = bool(record and record.get("how") == "own" and record.get("md5") == digest)
                now = {"kind": "folder", "label": "Своя" if mine else "В папке", "path": str(shown),
                       "k": "f-" + TC.stamp(self.folder), "file": shown.name}
                options.insert(0, now)
            if now is not None:
                now["note"] = f"сейчас · в папке — {shown.name}" if shown is not None else "сейчас"
        else:
            if record and record.get("how") == "own":
                now = {"kind": "own", "label": "Своя", "file": record.get("file") or "",
                       "k": "o-" + str(record.get("file") or "")[:12]}
                options.insert(0, now)
            elif record and record.get("how") == "site":
                now = site_of(record)
            if now is None:
                now = next((o for o in options if o["site"] == self.home), None)
            if now is not None:
                now["note"] = "сейчас"
        for option in options:
            option["now"] = option is now
        return options

    def back_label(self, options: list) -> str:
        """The site the title is of — «Вернуть обложку сайта» offered when its cover is not the one now."""
        home = next((o for o in options if o["kind"] == "site" and o["site"] == self.home), None)
        return home["label"] if home is not None and not home["now"] else ""

    def picture(self, option: dict) -> "tuple | None":
        from core import title_covers as TC
        if option["kind"] == "folder":
            try:
                data = Path(option["path"]).read_bytes()
            except OSError:
                return None
            return data, TC.content_type(TC.picture_ext(data))
        if option["kind"] == "own":
            data = TC.own_bytes(option.get("file") or "")
            return (data, TC.content_type(TC.picture_ext(data))) if data else None
        found = self.found
        if found.cover and found.cover_from.get("how") == "site" and found.cover_from.get("url") == option["url"]:
            return bytes(found.cover), TC.content_type(TC.picture_ext(found.cover))
        return TC.fetch(option["url"], option.get("page") or "", option.get("site") or "", option.get("key") or "")

    def put(self, data: bytes, ext: str, entry: dict, label: str, back: bool = False) -> dict:
        """The picture chosen (``entry`` — where from: {how: site|own, …}): into the folder (the one before beside) or
        kept by the program (``back`` — the site's again: nothing kept); the page's title shows it at once (its
        search's ``Found``)."""
        from core import title_covers as TC
        digest = TC.md5_of(data)
        if self.kind:
            result = TC.put(self.folder, self.stem, data, ext)
            TC.remember(self.ids, **entry, md5=TC.md5_of(Path(result["path"]).read_bytes()), folder=str(self.folder),
                        name=self.found.title.title)
            _libraries_changed(self.kind)
            said = _words_of(result, Path(result["path"]).name, label)
        elif back:
            TC.forget(self.ids)
            said = f"Обложка — снова {label}"
        else:
            TC.remember(self.ids, **entry, md5=digest, name=self.found.title.title)
            said = (f"Обложка — {label}. Тайтла ещё нет в папке — программа запомнила выбор: когда он скачается, "
                    f"в папку ляжет эта обложка")
        came = {k: v for k, v in entry.items() if k in ("how", "site", "url", "file") and v}
        for found in _founds(self.entry, self.found):
            found.cover, found.cover_type, found.cover_from = data, TC.content_type(ext), dict(came)
        return {"said": said, "cover": f"/api/manhwa/find-cover?find={self.entry.task.id}&v={digest[:12]}"}


def title_picture(found, settings: dict) -> "tuple | None":
    """(bytes, type) of the cover of a title of the sites as it is now — its folder's (one of the program's folders),
    else the one the owner chose (kept by the program) when it is not the one the title was found with; None — the
    found one (``find-cover`` of the title's page: a choice made on another page, or while its search still went)."""
    from core import manhwa_download as D, title_covers as TC
    folder = Path(found.folder)
    if TC.in_library(folder, settings) and (folder / D.LB_DIR / D.SOURCE_FILE).is_file():
        shown = TC.current(folder)
        if shown is not None:
            try:
                data = shown.read_bytes()
            except OSError:
                return None
            return data, TC.content_type(TC.picture_ext(data))
    record = TC.chosen(D.cover_ids(found))
    if record and record.get("how") in ("site", "own") and record.get("md5") and \
            record.get("md5") != TC.md5_of(found.cover or b""):
        return TC.picture_of(record)
    return None


def _founds(entry, found) -> list:
    """The title of a search as the page has it — and the search's own when it has ended (and the ones kept for «уже
    открывали»): each shows the new cover."""
    from webui.pages import manhwa_get as MG
    out = [found]
    kept = MG._found_of(entry.task) if entry is not None and entry.task is not None else None
    if kept is not None and kept.get("found") is not None and kept["found"] is not found:
        out.append(kept["found"])
    with MG._FOUND_LOCK:
        for _when, other in MG._FOUND.values():
            if all(other is not x for x in out) and other.title.site == found.title.site and \
                    other.title.key == found.title.key:
                out.append(other)
    return out


def _libraries_changed(kind: str) -> None:
    """The library of the folder reads it again (its cover's file is another one now)."""
    from core import manga_library as M, manhwa_library as H
    (M if kind == "manga" else H).LIBRARY.invalidate()


# ── a series (its folder, its site) ────────────────────────────────────────────────────────────────────────────────
class _Series:
    """The poster of a series: its folder's (``poster.*``), its site's — YummyAnime's (its answer names it) and
    AniLibria's on AniLiberty (when YummyAnime names its release and AniLiberty is on), Hentasis' page's."""

    def __init__(self, req: Request, ref):
        from core import video_watch as W
        self.req, self.ref = req, ref
        series = ref.series
        self.series = series
        self.title = None
        if ref.url:
            self.title = W.kept(ref.url)
            if self.title is None:
                from webui.pages.video_series import site_title
                self.title = site_title(req, ref, wait=SITE_WAIT)[0]
        if series is not None:
            path = Path(series.path)
            self.folder, self.stem = (path.parent, f"{path.stem}-poster") if series.single else (path, "poster")
        else:
            self.folder, self.stem = None, "poster"
        self.ids = ([f"folder:{series.id}"] if series is not None else []) + ([ref.key] if ref.url else [])
        self.home = self.title.site if self.title is not None else ""

    @property
    def name(self) -> str:
        if self.series is not None:
            return self.series.title
        return self.title.name if self.title is not None else "Сериал"

    def _sites(self) -> list:
        """The posters the site's answer names: [{"site", "label", "url", "page"}]."""
        from core import video_watch as W
        title = self.title
        if title is None:
            return []
        out = []
        if title.page:
            picture = W._page_poster(title.meta, title.address)
            if picture:
                out.append({"site": title.key.split(":", 1)[0], "label": title.site, "url": picture,
                            "page": title.address})
            return out
        from core import video_dubs as Y
        poster = title.anime.get("poster") if isinstance(title.anime.get("poster"), dict) else {}
        picture = Y.absolute(poster.get("fullsize") or poster.get("big") or poster.get("medium") or "")
        if picture:
            out.append({"site": "yummy", "label": title.site, "url": picture, "page": title.address})
        from core import video_sources
        remote = title.anime.get("remote_ids") if isinstance(title.anime.get("remote_ids"), dict) else {}
        alias = str(remote.get("anilibria_alias") or "").strip()
        if alias and video_sources.enabled(self.req.settings):
            out.append({"site": "aniliberty", "label": "AniLiberty", "url": "", "alias": alias,
                        "page": video_sources.site_of(self.req.settings)})
        return out

    def options(self) -> list:
        from core import title_covers as TC
        options = [dict(c, kind="site", k="s-" + _key(c.get("url") or c.get("alias") or "")) for c in self._sites()]
        record = TC.chosen(self.ids)
        now = None

        def site_of(entry: dict):
            return next((o for o in options if o["site"] == entry.get("site") and o.get("url") == entry.get("url")),
                        None) or next((o for o in options if o["site"] == entry.get("site")), None)
        series = self.series
        if series is not None:
            shown = Path(series.poster) if series.poster else None
            digest = TC._md5_file(shown) if shown is not None else ""
            if shown is not None and record and record.get("md5") == digest and record.get("how") == "site":
                now = site_of(record)
            info = series.info if isinstance(series.info, dict) else {}
            if now is None and shown is not None and info.get("poster_md5") == digest and info.get("poster_from"):
                now = next((o for o in options if o.get("url") == info.get("poster_from")), None)   # the description's
            if now is None and shown is not None:
                mine = bool(record and record.get("how") == "own" and record.get("md5") == digest)
                now = {"kind": "folder", "label": "Своя" if mine else "В папке", "path": str(shown),
                       "k": "f-" + _key(f"{shown}:{digest}"), "file": shown.name}
                options.insert(0, now)
            if now is not None:
                now["note"] = f"сейчас · {shown.name}" if shown is not None else "сейчас"
        else:
            if record and record.get("how") == "own":
                now = {"kind": "own", "label": "Своя", "file": record.get("file") or "",
                       "k": "o-" + str(record.get("file") or "")[:12]}
                options.insert(0, now)
            elif record and record.get("how") == "site":
                now = site_of(record)
            if now is None:
                now = next((o for o in options if o["kind"] == "site"), None)
            if now is not None:
                now["note"] = "сейчас"
        for option in options:
            option["now"] = option is now
        return options

    def back_label(self, options: list) -> str:
        home = next((o for o in options if o["kind"] == "site"), None)
        return home["label"] if home is not None and not home["now"] else ""

    def picture(self, option: dict) -> "tuple | None":
        from core import title_covers as TC
        if option["kind"] == "folder":
            try:
                data = Path(option["path"]).read_bytes()
            except OSError:
                return None
            return data, TC.content_type(TC.picture_ext(data))
        if option["kind"] == "own":
            data = TC.own_bytes(option.get("file") or "")
            return (data, TC.content_type(TC.picture_ext(data))) if data else None
        url = option.get("url") or ""
        if not url and option.get("alias"):
            url = _aniliberty_poster(option["alias"], option.get("page") or "")
            option["url"] = url
        return TC.fetch(url, option.get("page") or "") if url else None

    def put(self, data: bytes, ext: str, entry: dict, label: str, back: bool = False) -> dict:
        from core import title_covers as TC, video_library as V
        digest = TC.md5_of(data)
        series = self.series
        if series is not None:
            before = _poster_now(self.folder, self.stem)
            result = TC.put(self.folder, self.stem, data, ext)
            written = Path(result["path"])
            from core import video_meta_import as I
            _poster_kept(I.info_path(series), before, written)
            TC.remember(self.ids, **entry, md5=TC.md5_of(written.read_bytes()), folder=str(self.folder),
                        name=self.name)
            V.LIBRARY.invalidate()
            st = written.stat()
            return {"said": _words_of(result, written.name, label),
                    "cover": f"/videos/cover/{series.id}?size=l&v={int(st.st_mtime)}-{st.st_size}"}
        if back:
            TC.forget(self.ids)
            said = f"Обложка — снова {label}"
            shown = TC.catalog_id({"how": "site", "url": entry.get("url"), "page": entry.get("page")})
        else:
            record = TC.remember(self.ids, **entry, md5=digest, name=self.name)
            said = (f"Обложка — {label}. Сериала ещё нет в папке — программа запомнила выбор: когда он скачается, в "
                    f"папку ляжет эта обложка")
            shown = TC.catalog_id(record)
        if shown and self.ref.url:
            V.PROGRESS.note(self.ref.key, cover=shown)          # the shelf «Продолжить просмотр» shows it too
        return {"said": said, "cover": f"/api/catalog/cover?c={quote(shown, safe='')}" if shown else ""}


def series_picture(address: str) -> "tuple | None":
    """(bytes, type) of the poster the owner chose for the series of a site's page (``address``) — «Загрузки» show it
    for the series going: its folder's when the program put it there, else the one kept for it; None — none chosen."""
    from core import title_covers as TC, video_watch as W
    key = W.key_of(str(address or ""))
    choice = TC.chosen([key]) if key else None
    if choice is None or choice.get("how") not in ("site", "own"):
        return None
    if choice.get("folder"):
        path = TC.current(Path(str(choice["folder"])), "poster")
        try:
            data = path.read_bytes() if path is not None else b""
        except OSError:
            data = b""
        return (data, TC.content_type(TC.picture_ext(data))) if data else None
    return TC.picture_of(choice)


def _poster_now(folder: Path, stem: str) -> "tuple | None":
    """(the name, the md5) of the folder's poster now — None: none."""
    from core import title_covers as TC
    path = TC.current(folder, stem)
    return (path.name, TC._md5_file(path)) if path is not None else None


def _poster_kept(info_file: Path, before, written: Path) -> None:
    """«Описание с сайта» (every «Скачать» of the series asks it) writes over the poster its import wrote —
    ``video_meta_import._ours``: by its name, and by its bytes when they were noted; an ``info.json`` of before has no
    bytes noted.  A poster chosen here under the name of the import's would be written over then — the bytes of the
    import's one (beside now) are noted: the chosen one is the person's own, the next «Скачать» keeps it."""
    if before is None or not before[1] or written.name != before[0]:
        return
    import json
    from core import video_meta_import as I
    with I.INFO_LOCK:
        try:
            info = json.loads(info_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        if not isinstance(info, dict) or str(info.get("poster_file") or "") != before[0] or info.get("poster_md5"):
            return
        info["poster_md5"] = before[1]
        try:
            I._write(info_file, json.dumps(info, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError as exc:                      # words, not the file's path
            _LOG.warning("info.json of %s not written: %s", info_file.parent.name, exc.strerror or exc)


def _aniliberty_poster(alias: str, site: str) -> str:
    """The poster of AniLibria's release on AniLiberty (its API's answer, asked once a while)."""
    with _RELEASES_LOCK:
        kept = _RELEASES.get(alias)
    if kept is not None and time.monotonic() - kept[0] < PICTURE_TTL:
        return kept[1]
    from core import video_sources
    try:
        release = video_sources.AniLiberty(site).release(alias)
    except video_sources.SourceError as exc:
        _LOG.info("AniLiberty's poster of %s: %s", alias, exc)
        return ""
    poster = release.get("poster") if isinstance(release, dict) and isinstance(release.get("poster"), dict) else {}
    optimized = poster.get("optimized") if isinstance(poster.get("optimized"), dict) else {}
    src = str(poster.get("src") or optimized.get("src") or poster.get("preview") or "").strip()
    address = urljoin(site.rstrip("/") + "/", src) if src else ""
    with _RELEASES_LOCK:
        _RELEASES[alias] = (time.monotonic(), address)
    return address


# ── which title, which option ───────────────────────────────────────────────────────────────────────────────────────
def _subject(req: Request, body: "dict | None" = None):
    """(the title or the series, None) — or (None, the refusal) for the page."""
    body = body if body is not None else {}
    find = str(body.get("find") or req.arg("find") or "")
    if find:
        from core import content_marks, manhwa_sources as S
        from types import SimpleNamespace
        from webui import content_mode
        from webui.pages import manhwa_get as MG
        entry = MG._find_of(find, newest=False)
        found = entry.now()[0] if entry is not None else None
        if found is None:
            return None, Response.json({"ok": False, "error": MG.STALE}, 409)
        hentai = bool(content_marks.manhwa_reason(SimpleNamespace(info=found.title.info()))) or \
            bool(getattr(S.by_name(found.title.site), "adult_only", False))
        if not content_mode.shows(hentai):
            return None, Response.json({"ok": False, "hidden": True, "error": "Скрыто режимом"}, 403)
        return _Title(req, entry, found), None
    from webui.pages import video_series as VS
    ref = VS._ref_of(req, body)
    if ref is None:
        return None, Response.json({"ok": False, "error": "Такого сериала нет"}, 404)
    if VS._folder_hidden(ref):
        return None, Response.json({"ok": False, "hidden": True, "error": "Скрыто режимом"}, 403)
    subject = _Series(req, ref)
    if ref.series is None and VS._hidden_title(subject.title):
        return None, Response.json({"ok": False, "hidden": True, "error": "Скрыто режимом"}, 403)
    return subject, None


def _where(subject) -> str:
    """The address's part that names the title (for its pictures)."""
    if isinstance(subject, _Title):
        return f"find={subject.entry.task.id}"
    if subject.series is not None:
        return f"series={subject.series.id}"
    return "url=" + quote(subject.ref.url, safe="")


def listing(req: Request) -> Response:
    """The options of the menu: [{k, kind, label, note, now}], where the cover goes (its folder or the program), the
    site «Вернуть обложку сайта» names."""
    subject, refused = _subject(req)
    if refused is not None:
        return refused
    options = subject.options()
    folder = bool(subject.kind) if isinstance(subject, _Title) else subject.series is not None
    stem = subject.stem + ".jpg"
    return Response.json({"ok": True, "title": subject.name, "folder": folder, "file": stem,
                          "series": not isinstance(subject, _Title),
                          "options": [{"k": o["k"], "kind": o["kind"], "label": o["label"],
                                       "note": o.get("note") or "", "now": bool(o["now"])} for o in options],
                          "back": subject.back_label(options), "pic": f"/api/covers/pic?{_where(subject)}&k="})


def _option(subject, key: str) -> "dict | None":
    return next((o for o in subject.options() if o["k"] == str(key or "")), None)


def picture(req: Request) -> Response:
    subject, refused = _subject(req)
    if refused is not None:
        return Response.not_found("Обложки нет")
    option = _option(subject, req.arg("k"))
    got = subject.picture(option) if option is not None else None
    if got is None:
        return Response.not_found("Обложка не пришла")
    return Response(200, bytes(got[0]), got[1], {"Cache-Control": "private, max-age=600"})


def choose(req: Request) -> Response:
    """{find | series | url, k} — that option's cover; {…, back: true} — the title's site's again."""
    from core import title_covers as TC
    body = req.json()
    subject, refused = _subject(req, body)
    if refused is not None:
        return refused
    options = subject.options()
    if body.get("back"):
        option = next((o for o in options if o["kind"] == "site" and (not isinstance(subject, _Title)
                                                                       or o["site"] == subject.home)), None)
        if option is None:
            return Response.json({"ok": False, "error": "У сайта этого тайтла обложки нет"}, 409)
    else:
        option = next((o for o in options if o["k"] == str(body.get("k") or "")), None)
        if option is None:
            return Response.json({"ok": False, "error": "Такой обложки в меню уже нет — откройте его снова"}, 409)
    got = subject.picture(option)
    if got is None:
        return Response.json({"ok": False, "error": f"Обложка с {option['label']} не пришла — попробуйте ещё раз"}, 502)
    if option["now"] and option["kind"] != "site":
        return Response.json({"ok": True, "said": "Эта обложка и стоит", "cover": ""})
    try:
        data, ext = TC.prepared(got[0])
        if option["kind"] == "site":
            entry = {"how": "site", "site": option["site"], "label": option["label"], "url": option.get("url") or "",
                     "page": option.get("page") or "", "key": option.get("key") or ""}
            label = f"с {option['label']}"
        else:
            entry, label = {"how": "own", "file": option.get("file") or ""}, "своя картинка"
        result = subject.put(data, ext, entry, label, back=bool(body.get("back")))
    except TC.CoverError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, **result})


def own(req: Request) -> Response:
    """«Своя картинка…»: the body is the picture (a file of the computer, a photo of the phone)."""
    from core import title_covers as TC
    subject, refused = _subject(req)
    if refused is not None:
        return refused
    try:
        data, ext = TC.prepared(req.body)
        entry = {"how": "own"}
        if not (subject.kind if isinstance(subject, _Title) else subject.series is not None):
            entry["file"] = TC.own_store(data, ext)       # no folder: the program keeps the picture itself
        result = subject.put(data, ext, entry, "своя картинка")
    except TC.CoverError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Картинка не сохранилась: {exc.strerror or exc}"}, 500)
    return Response.json({"ok": True, **result})


# ── a series chosen for while it had no folder: its folder now ──────────────────────────────────────────────────────
def _video_ended(task) -> None:
    """A download of a series has ended (the other chat's: its result and its folder are only read): the poster the
    owner chose while the series had no folder goes into it (the one the download wrote — beside)."""
    import json
    from core import title_covers as TC, video_library as V, video_watch as W
    result = task.result if isinstance(task.result, dict) else {}
    folder = Path(str(result.get("folder") or ""))
    if not str(result.get("folder") or "") or not folder.is_dir():
        return
    try:
        info = json.loads((folder / V.INFO_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    key = W.key_of(str(info.get("source_url") or "")) if isinstance(info, dict) else ""
    choice = TC.chosen([key]) if key else None
    if choice is None or choice.get("folder") or choice.get("how") not in ("site", "own"):
        return
    got = TC.picture_of(choice)
    if got is None:
        return
    try:
        data, ext = TC.prepared(got[0])
        before = _poster_now(folder, "poster")        # the one the download's «Описание с сайта» wrote
        result = TC.put(folder, "poster", data, ext)
        _poster_kept(folder / V.INFO_NAME, before, Path(result["path"]))
    except (TC.CoverError, OSError) as exc:          # words, not the file's path
        _LOG.warning("the poster chosen for %s not put into its folder: %s", folder.name,
                     getattr(exc, "strerror", None) or exc)
        return
    from core.settings import load_settings
    root = Path(V.video_root(load_settings()))
    ids = [key] + ([f"folder:{V._id_for(root, folder)}"] if root.is_dir() else [])
    TC.remember(ids, **{k: v for k, v in choice.items() if k not in ("ids", "at", "md5", "folder")},
                md5=TC.md5_of(Path(result["path"]).read_bytes()), folder=str(folder))
    V.LIBRARY.invalidate()
