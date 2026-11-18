"""(v5.44.93; its own file since v5.44.147) What is downloaded already is not in «Лента» — the owner: «оно не должно
показывать то что уже скачано».  :func:`downloaded`, :class:`Have`: the titles of the program's folders; a tile of one
of them is left out (``catalog`` — «Уже скачанные не показаны: N»).  Here too — how the address of a title of a site of
video tells its number or slug (the folder of a series keeps the address it was downloaded from)."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

_LOG = logging.getLogger("local_booru.catalog")

HAVE_TTL = 60.0                   # the titles in the program's folders, kept this long (seconds)


@dataclass
class Have:
    """(v5.44.93) The titles of the program's folders: (site, key) of the ones downloaded from the sites (and of their
    other sites), the name keys (``manhwa_sources.names_of``) of every one there."""
    keys: set = field(default_factory=set)
    names: set = field(default_factory=set)
    primary_keys: set = field(default_factory=set)    # downloaded identities, without merely discovered helpers

    def holds(self, ids, names) -> bool:
        from core.manhwa_sources import title_identity
        identities = {title_identity(site, key) for site, key in ids or ()}
        return bool(identities & self.keys) or bool(set(names or ()) & self.names)


_HAVE: dict = {}                                  # (the kind's group, its folders) → (when, Have, their times)
_HAVE_LOCK = threading.Lock()


def downloaded(kind: str, settings: "dict | None") -> Have:
    """(v5.44.93) What the folders of the kind hold — the owner: «оно не должно показывать то что уже скачано».  The
    manga and the manhwa (both folders for both lists: a title goes to either by its kind): ``.lb/source.json`` of
    every title the program downloaded or linked (its site and key, and its other sites'), the names of every manga
    (``title``, nhentai's ``title_pretty``, ``title_japanese``) and manhwa (its name and ``alt_titles``); the
    videos: every series — ``source_url`` (YummyAnime's alias, Hentasis's number) and its names.  Kept
    :data:`HAVE_TTL` seconds (the libraries scan in the background, as their tabs do)."""
    settings = settings or {}
    if kind == "video":
        from core.video_library import video_root
        group, roots = "video", (video_root(settings),)
    else:
        from core import manhwa_download as D
        folders = D.roots(settings)
        group, roots = "books", (folders.get("manhwa") or "", folders.get("manga") or "")
    sig = (group, roots)
    stamp = tuple(_mtime(root) for root in roots)      # a title folder added or taken away — read again at once
    with _HAVE_LOCK:
        kept = _HAVE.get(sig)
        if kept is not None and time.monotonic() - kept[0] < HAVE_TTL and kept[2] == stamp:
            return kept[1]
    try:
        have = _have_videos(roots[0]) if group == "video" else _have_books(settings, roots)
    except Exception as exc:                          # noqa: BLE001 — a folder that cannot be read hides nothing
        _LOG.warning("catalog: the folders not read: %s", exc, exc_info=True)
        have = Have()
    with _HAVE_LOCK:
        _HAVE[sig] = (time.monotonic(), have, stamp)
    return have


def _mtime(path: str) -> int:
    from pathlib import Path
    try:
        return Path(path).stat().st_mtime_ns if path else 0
    except OSError:
        return 0


def forget_have() -> None:
    with _HAVE_LOCK:
        _HAVE.clear()


def _have_books(settings: dict, roots: tuple) -> Have:
    from core import manga_library as M, manhwa_download as D, manhwa_library as H, manhwa_sources as S
    have = Have()
    for _folder, data in D.followed(settings):
        identity = S.title_identity(data.get("site"), data.get("key"))
        have.keys.add(identity)
        have.primary_keys.add((str(data.get("site")), str(data.get("key"))))
        for other in data.get("helpers") or []:
            if isinstance(other, dict) and other.get("site") and other.get("key"):
                have.keys.add(S.title_identity(other["site"], other["key"]))
        have.names |= S.names_of(str(data.get("title") or ""))
    manhwa, manga = roots
    if manhwa and _is_dir(manhwa):
        for title in H.LIBRARY.items(manhwa):
            have.names |= S.names_of(title.title, *title.alt_titles)
            _book_sources(have, title.info.get("source_url"), title.chapters)
    if manga and _is_dir(manga):
        for item in M.LIBRARY.items(manga):
            have.names |= S.names_of(item.title, item.title_pretty, item.title_japanese)
            _book_sources(have, item.source_url, [item, *item.chapters], primary_nhentai=True)
    return have


def _book_sources(have: Have, address, chapters, *, primary_nhentai: bool = False) -> None:
    """Recognize saved title and downloaded-chapter URLs through every registered source."""
    from core import manhwa_sources as S
    addresses = [address]
    for chapter in chapters:
        info = chapter.info if isinstance(chapter.info, dict) else {}
        source = info.get("source")
        if isinstance(source, dict):
            addresses.append(source.get("url"))
    for position, url in enumerate(addresses):
        try:
            parsed = urlparse(str(url or ""))
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                continue
            source, key = S.source_for(str(url))
            identity = S.title_identity(source.name, key) if source is not None and key else None
            if parsed.hostname.lower() in ("nhentai.net", "www.nhentai.net"):
                gallery = re.fullmatch(r"/g/(\d+)(?:/\d+)?/?", parsed.path)
                if gallery:
                    identity = ("nhentai", str(int(gallery.group(1))))
        except ValueError:
            continue
        if identity:
            have.keys.add(identity)
            # primary_keys is also used before removing a matched loose picture. Partial chapter
            # sources may hide a feed card, but must never claim the whole title was downloaded.
            if primary_nhentai and position == 0 and identity[0] == "nhentai":
                have.primary_keys.add(identity)


def _have_videos(root: str) -> Have:
    from core import manhwa_sources as S, video_dubs, video_library as V
    have = Have()
    if not root or not _is_dir(root):
        return have
    for series in V.LIBRARY.items(root):
        source = str(series.info.get("source_url") or "")
        alias = video_dubs.alias_of(source) if source else ""
        if alias:
            have.keys.add(("yummy", alias))
        number = hentasis_number(source)
        if number:
            have.keys.add(("hentasis", number))
        slug = haven_slug(source)                   # (v5.44.138)
        if slug:
            have.keys.add(("haven", slug))
        number = xs_number(source)                  # (v5.44.139)
        if number:
            have.keys.add(("hentaixs", number))
        number = hfshare_number(source)
        if number:
            have.keys.add(("hfshare", number))
        have.names |= S.names_of(series.title, series.folder_title, *series.alt_titles)
    return have


def hentasis_number(address: str) -> str:
    """The number of a title of Hentasis in its address («…/1112-dark-summoner-to-dekiteiru.html»; a mirror —
    «hentasisN.top» — too), else ""."""
    from core.catalog import HENTASIS_SITE
    try:
        parsed = urlparse(str(address or ""))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not host or not ("hentasis" in host or host == (urlparse(HENTASIS_SITE).hostname or "").lower()):
        return ""
    number = re.search(r"/(\d+)-[^/]*\.html?$", parsed.path)
    return number.group(1) if number else ""


def haven_slug(address: str) -> str:
    """(v5.44.138) The slug of a title of HentaiHaven in its address («…/watch/<slug>/», «…/ru/watch/<slug>/episode-2/»),
    else ""."""
    from core.catalog import HAVEN_SITE
    try:
        parsed = urlparse(str(address or ""))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if not (host == "hentaihaven.xxx" or host.endswith(".hentaihaven.xxx")
            or parsed.netloc.lower() == urlparse(HAVEN_SITE).netloc.lower()):
        return ""
    found = re.match(r"(?:/[a-z]{2})?/watch/([a-z0-9][a-z0-9-]{0,199})(?:/|$)", parsed.path)
    return found.group(1) if found else ""


def site_host(address: str, site: str, own: str) -> "tuple | None":
    """(the parsed address, its path) when it is of the site (its host ``own``, a host under it, or the address the
    program asks it at — ``site``); else None."""
    try:
        parsed = urlparse(str(address or ""))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host or not (host == own or host.endswith("." + own) or parsed.netloc.lower() == urlparse(site).netloc.lower()):
        return None
    return parsed, parsed.path


def xs_number(address: str) -> str:
    """(v5.44.139) The number of a title of Hentai-XS in its address («…/anime.php?id=850»), else ""."""
    from core.catalog import XS_SITE
    got = site_host(address, XS_SITE, "hentai-xs.com")
    if got is None or not re.fullmatch(r"/anime\.php", got[1]):
        return ""
    number = re.search(r"(?:^|&)id=(\d{1,9})(?:&|$)", got[0].query)
    return str(int(number.group(1))) if number else ""


def hfshare_number(address: str) -> str:
    """(v5.44.139) The number of a title of HFShare in its address («…v2.hfshare.me/6878-muramata-san-no-aijou.html»),
    else ""."""
    from core.catalog import HFSHARE_SITE
    got = site_host(address, HFSHARE_SITE, "hfshare.me")
    if got is None:
        return ""
    number = re.fullmatch(r"/(\d{1,9})-[^/]*\.html?", got[1])
    return number.group(1) if number else ""


def _is_dir(path: str) -> bool:
    from pathlib import Path
    try:
        return Path(path).is_dir()
    except OSError:
        return False
