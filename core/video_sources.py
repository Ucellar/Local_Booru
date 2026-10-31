"""Where the episodes of a title come from when its page has no video of its own («Скачать с сайта»).

YummyAnime shows its episodes in players of other sites (Kodik and the like) — the program does not take video from
those (``core/video_download``).  Its page names the title exactly, though: by the title's numbers on Shikimori and
MyAnimeList (its links «Shikimori 8.8», «MyAnimeList 9.2» — ``video_meta.page_ids``).  The same title is looked up on a
site that gives its episodes openly and they are downloaded from there into the series' folder; the description, the
genres and the poster stay the page's (``Found.about`` — what ``video_download._describe`` writes).

The source — AniLiberty (AniLibria until 2025; its address is ``aniliberty_url`` in «Настройки → Вкладка «Видео»»,
anilibria.top is its mirror; ``video_source_aniliberty`` switches the search off) — is asked through the public API its
own programs use (``<address>/api/v1``, JSON):

* ``/app/search/releases?query=…`` — up to 20 releases by a Russian or an English name or the alias.  Asked by the
  page's title, then by its other titles in letters the search knows (a name with punctuation also without it: the
  search finds «Re: Zero», not «Re:Zero»), until the title is found;
* a release is the title when its ``shikimori.id`` or ``mal.id`` is the page's.  A release with other numbers is
  another title (another season, a film of the series) and is never taken for it.  When there are no numbers to
  compare, a release whose name is one of the page's and whose year is the page's is taken — and it is said so;
* ``/anime/releases/{id}`` — the release with its episodes: ``hls_1080``/``hls_720``/``hls_480`` (open MPEG-TS streams
  of its video server — ``core/video_hls``: the best first, the next when one does not come), ``ordinal``, ``name``,
  ``duration``;
* a release the rights holder had taken off (``is_blocked_by_copyrights``) or one closed in the person's country
  (``is_blocked_by_geo``) is said in words and left alone — no other address and no other way are tried.

A page of AniLiberty itself (``/anime/releases/release/<alias>/…``) is its release by the alias; the description and the
poster are AniLiberty's then: a page made of the API's answer in YummyAnime's markup (``release_page``), which
``video_meta`` reads as it reads YummyAnime.

The episodes go to the sub-folder of the voice-over, «Озвучка AniLiberty» — a tab of the series beside the other
voice-overs of the title (a source that gives several makes a tab for each); those numbered with a fraction (12.5) —
to «Озвучка AniLiberty — спецвыпуски», so that the numbers of a tab stay whole.  The files are named by the number
alone («Серия 01.ts»): the next search names an episode the same way, so what is in the folder already is not
downloaded again (the title of an episode on the site may change).  The voice-overs the page lists (YummyAnime's
«Озвучка») are shown with the one a source gives marked — the rest are only in the page's closed player.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import quote, urlparse

from core import video_download, video_meta

_LOG = logging.getLogger("local_booru.video")

NAME = "AniLiberty"
DEFAULT_SITE = "https://aniliberty.top"
HOSTS = ("aniliberty.top", "anilibria.top", "anilibria.tv")    # its addresses (and the one in the settings)
API = "/api/v1"
TIMEOUT = (15, 40)
MAX_ANSWER = 16 * 1024 * 1024     # bytes of one answer of the API (a long series lists a thousand episodes)
MAX_QUERIES = 6                   # searches for one title at most
MAX_QUERY = 120                   # characters of one search
QUALITIES = (1080, 720, 480)
OWN_NAMES = ("anilibria", "aniliberty", "анилибрия")   # how sites name its voice-over
DUB = f"Озвучка {NAME}"            # the sub-folder of its episodes: a tab of the series, beside other voice-overs
SPECIALS = " — спецвыпуски"       # … and of its episodes numbered with a fraction (12.5): a tab of their own
_RELEASE_PATH = re.compile(r"^/anime/releases/release/([A-Za-z0-9][A-Za-z0-9_-]{0,200})(?:/|$)")
_OLD_RELEASE_PATH = re.compile(r"^/release/([A-Za-z0-9][A-Za-z0-9_-]{0,200})\.html$")
KINDS = {"TV": "ТВ", "ONA": "ONA", "OVA": "OVA", "MOVIE": "фильм", "SPECIAL": "спешл"}


class SourceError(Exception):
    """Said to the person as it is."""


@dataclass
class Title:
    """What the page says of its title."""
    names: list = field(default_factory=list)     # its title first, then its other titles
    year: int = 0
    kind: str = ""                                 # "TV", "MOVIE", "OVA", "ONA", "SPECIAL" or ""
    ids: dict = field(default_factory=dict)        # {"shikimori": n, "mal": n}


# ── settings ──────────────────────────────────────────────────────────────────────
def site_address(value) -> str:
    """``https://host`` of AniLiberty from the settings (a bare host gets https://); the usual one when it is wrong."""
    text = str(value or "").strip()
    if text and "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
        host = parsed.hostname or ""
        parsed.port                                # «host:abc» is no address
    except ValueError:                             # «https://[»
        return DEFAULT_SITE
    named = bool(re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+|localhost|[0-9a-f:.]*:[0-9a-f:.]*", host))
    if parsed.scheme not in ("http", "https") or not named or any(c in text for c in " \"'<>\\@"):
        return DEFAULT_SITE
    return f"{parsed.scheme}://{parsed.netloc}"


def enabled(settings: dict) -> bool:
    return bool((settings or {}).get("video_source_aniliberty", True))


def site_of(settings: dict, say=None) -> str:
    """AniLiberty's address from the settings; one that is not an address is said and the usual one taken."""
    given = str((settings or {}).get("aniliberty_url") or "").strip()
    site = site_address(given)
    if given and site == DEFAULT_SITE and given.rstrip("/") not in (DEFAULT_SITE, DEFAULT_SITE.split("://")[1]):
        (say or _LOG.info)(f"Адрес {NAME} в настройках («{given[:80]}») не похож на адрес сайта — беру {DEFAULT_SITE}")
    return site


def _hosts(site: str) -> set:
    return {*HOSTS, (urlparse(site).hostname or "").lower().removeprefix("www.")}


def release_alias(url: str, site: str = DEFAULT_SITE) -> str:
    """The alias of the release an AniLiberty page is about (``…/anime/releases/release/<alias>/episodes``), else ""."""
    try:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""
    if host not in _hosts(site):
        return ""
    found = _RELEASE_PATH.match(parsed.path or "") or _OLD_RELEASE_PATH.match(parsed.path or "")
    return found.group(1) if found else ""


# ── what the page says ────────────────────────────────────────────────────────────
def kind_of(text: str) -> str:
    low = str(text or "").lower()
    for marks, kind in ((("фильм", "movie", "полнометраж"), "MOVIE"), (("ova", "oad"), "OVA"),
                        (("ona", "web"), "ONA"), (("спешл", "special", "спецвыпуск"), "SPECIAL"),
                        (("tv", "тв", "сериал"), "TV")):
        if any(mark in low for mark in marks):
            return kind
    return ""


def title_of(meta: dict) -> Title:
    names = []
    for name in [meta.get("title")] + list(meta.get("alt_titles") or []):
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    year = re.search(r"\b(19[3-9]\d|20\d\d)\b", str(meta.get("year") or ""))
    ids = {k: v for k, v in dict(meta.get("ids") or {}).items() if isinstance(v, int) and v > 0}
    return Title(names=names, year=int(year.group(1)) if year else 0, kind=kind_of(str(meta.get("type") or "")),
                 ids=ids)


def norm(name: str) -> str:
    """A name as names are compared: small letters, «ё» as «е», no punctuation."""
    text = str(name or "").lower().replace("ё", "е")
    return re.sub(r"[\W_]+", " ", text).strip()


def queries(title: Title) -> list[str]:
    """What to ask the search, in order: the names in letters it knows (not Japanese), as written and without
    punctuation."""
    out: list[str] = []
    for name in title.names:
        if not re.search(r"[A-Za-zА-Яа-яЁё]", name):
            continue
        for variant in (name, re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", name)).strip()):
            variant = variant[:MAX_QUERY].strip()
            if len(variant) >= 2 and variant not in out:
                out.append(variant)
    return out[:MAX_QUERIES]


# ── AniLiberty ────────────────────────────────────────────────────────────────────
class AniLiberty:
    """Its public API (``<site>/api/v1``)."""

    def __init__(self, site: str = DEFAULT_SITE):
        self.site = site_address(site)
        self._session = None

    def _get(self, path: str, params: dict | None = None):
        """The JSON of an answer; None for «there is no such» (404)."""
        import requests
        from core.redaction import sanitize_text
        from core.video_meta_import import _session, guard_of
        if self._session is None:
            self._session = _session(self.site)
        try:
            with self._session.get(self.site + API + path, params=params, timeout=TIMEOUT, stream=True,
                                   headers={"Accept": "application/json"}) as answer:
                body = bytearray()
                for chunk in answer.iter_content(64 * 1024):
                    body += chunk
                    if len(body) > MAX_ANSWER:
                        raise SourceError(f"{NAME} прислал слишком большой ответ")
                status, ctype = answer.status_code, str(answer.headers.get("Content-Type") or "").lower()
        except requests.RequestException as exc:
            raise SourceError(f"{NAME} не ответил: {sanitize_text(str(exc))[:200]}") from exc
        text = bytes(body).decode("utf-8", "replace")
        if status == 429:
            raise SourceError(f"{NAME} просит подождать (слишком много запросов) — попробуйте через минуту")
        if "json" not in ctype and text.lstrip()[:1] not in ("{", "["):
            guard = guard_of(status, text)
            raise SourceError(f"{NAME} ответил страницей вместо данных ("
                              + (f"проверка на роботов: {guard}" if guard else f"ответ {status}") + ")")
        if status == 404:
            return None
        if status >= 400:
            raise SourceError(f"{NAME} ответил {status}")
        try:
            return json.loads(text)
        except ValueError:
            raise SourceError(f"{NAME} прислал непонятный ответ (не JSON)") from None

    def search(self, query: str) -> list[dict]:
        data = self._get("/app/search/releases", {"query": query})
        if isinstance(data, dict):
            data = data.get("data")
        return [r for r in data or [] if isinstance(r, dict)] if isinstance(data, list) else []

    def release(self, key) -> dict | None:
        data = self._get("/anime/releases/" + quote(str(key), safe=""))
        return data if isinstance(data, dict) and data.get("id") is not None else None


def ids_of(release: dict) -> dict:
    out = {}
    for key in ("shikimori", "mal"):
        part = release.get(key)
        value = part.get("id") if isinstance(part, dict) else None
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            out[key] = value
    return out


def names_of(release: dict) -> list[str]:
    name = release.get("name") if isinstance(release.get("name"), dict) else {}
    out = [name.get("main"), name.get("english"), name.get("alternative"), str(release.get("alias") or "")]
    return [str(n) for n in out if n]


def _year(release: dict) -> int:
    try:
        return int(release.get("year") or 0)
    except (TypeError, ValueError):
        return 0


def verdict(title: Title, release: dict) -> str:
    """"same" — the title by its number; "other" — another title by its number; "name" — no numbers to compare,
    the name and the year are the page's; "" — not this one."""
    theirs = ids_of(release)
    shared = [k for k in ("shikimori", "mal") if k in theirs and k in title.ids]
    if shared:
        return "same" if any(theirs[k] == title.ids[k] for k in shared) else "other"
    ours = {norm(n) for n in title.names} - {""}
    if not ours & {norm(n) for n in names_of(release)}:
        return ""
    year = _year(release)
    if title.year and year and year != title.year:
        return ""
    return "name"


def describe(release: dict) -> str:
    """«Провожающая в последний путь Фрирен» (2023, ТВ)"""
    name = (names_of(release) or [f"№{release.get('id')}"])[0]
    kind = release.get("type") if isinstance(release.get("type"), dict) else {}
    bits = [str(_year(release) or ""), str(kind.get("description") or KINDS.get(str(kind.get("value") or ""), ""))]
    bits = [b for b in bits if b]
    return f"«{name}»" + (f" ({', '.join(bits)})" if bits else "")


def find_release(api: AniLiberty, title: Title, *, say, stop) -> tuple[dict | None, str, list]:
    """(release from the search, "same" | "name", other titles of a similar name) — (None, "", …) when not found."""
    maybe, others = None, []
    for query in queries(title):
        if stop():
            break
        results = api.search(query)
        for release in results:
            how = verdict(title, release)
            if how == "same":
                return release, how, others
            if how == "name" and maybe is None:
                maybe = release
            if how == "other" and describe(release) not in others:
                others.append(describe(release))
        if maybe is not None and not title.ids:
            break                       # nothing to find a better one by
    return (maybe, "name", others) if maybe is not None else (None, "", others)


# ── its episodes ───────────────────────────────────────────────────────────────────
def _absolute(address, site: str) -> str:
    address = str(address or "").strip()
    if address.startswith("//"):
        return "https:" + address
    if address.startswith(("https://", "http://")):
        return address
    if address.startswith("/"):
        return site + address
    return ""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else float(value)


def episode_files(release: dict, site: str) -> list:
    """The episodes as files to download (``video_download.Remote``), in the release's order."""
    files: list = []
    episodes = [e for e in release.get("episodes") or [] if isinstance(e, dict)]
    episodes.sort(key=lambda e: (_number(e.get("sort_order")) or 0, _number(e.get("ordinal")) or 0))
    for place, episode in enumerate(episodes, 1):
        number = _number(episode.get("ordinal"))
        number = place if number is None or number < 0 else number
        label = f"{number:02d}" if isinstance(number, int) else f"{number:g}"
        name = video_download._unique(f"Серия {label}.mp4", files)
        heading = str(episode.get("name") or episode.get("name_english") or "").strip()
        links = [(f"{q}p", _absolute(episode.get(f"hls_{q}"), site)) for q in QUALITIES]
        links = [(q, url) for q, url in links if url]
        try:
            length = float(episode.get("duration") or 0)
        except (TypeError, ValueError):
            length = 0.0
        files.append(video_download.Remote(
            url=links[0][1] if links else "", name=name, title=f"Серия {number:g}" + (f" — {heading}" if heading else ""),
            stream="hls", quality=links[0][0] if links else "", alts=[[q, url] for q, url in links[1:]],
            group=DUB + SPECIALS if isinstance(number, float) else DUB, page=site + "/", length=length,
            blocked="" if links else f"{NAME} не даёт видео этой серии"))
    return files


def release_url(release: dict, site: str) -> str:
    return f"{site}/anime/releases/release/{quote(str(release.get('alias') or release.get('id') or ''))}/episodes"


def title_page(*, main: str, others: list, refs: list, rows: list, picture: str, text: str, url: str, origin: str):
    """A title's page made of what an API said, in the markup ``video_meta`` reads on YummyAnime (AniLiberty's release
    here, YummyAnime's own title in ``core/video_dubs``): ``refs`` — [(«Shikimori»|«MyAnimeList», address, rating)],
    ``rows`` — [(label, value, items)] of ``ul.content-main-info`` (a row with ``items`` is a list; «Озвучка:» —
    ``animeVoices``), ``text`` — the description, its paragraphs by lines."""
    from core.video_meta_import import SavedPage
    lines = []
    for label, value, items in rows:
        if items:
            inner = "".join(f"<li><a>{escape(i)}</a></li>" for i in items)
            lines.append(f'<li><span>{escape(label)}</span><ul{" class=animeVoices" if label == "Озвучка:" else ""}>'
                         f"{inner}</ul></li>")
        elif value:
            lines.append(f"<li><span>{escape(label)}</span><div>{escape(value)}</div></li>")
    links = [f'<a data-balloon="{label}" href="{escape(str(address))}"><span>{escape(str(rating or ""))}</span></a>'
             for label, address, rating in refs]
    paragraphs = "".join(f"<p>{escape(p.strip())}</p>" for p in re.split(r"\r?\n", str(text or "")) if p.strip())
    html = (f'<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>{escape(main)}</title>'
            f'<link rel="canonical" href="{escape(url)}"></head><body>'
            + (f'<div class="poster-block"><img data-full="{escape(picture)}" src="{escape(picture)}" alt=""></div>'
               if picture else "")
            + f'<h1 itemprop="name">{escape(main)}</h1>'
            + (f'<ul class="alt-names-list">{"".join(f"<li>{escape(o)}</li>" for o in others)}</ul>' if others else "")
            + (f'<div class="content-ref-ids">{"".join(links)}</div>' if links else "")
            + f'<ul class="content-main-info">{"".join(lines)}</ul>'
            + (f'<div class="content-desc"><div id="content-desc-text">{paragraphs}</div></div>' if paragraphs else "")
            + "</body></html>")
    return SavedPage(html=html, url=url, origin=origin)


def release_page(release: dict, site: str):
    """A page of the release made of the API's answer, in the markup ``video_meta`` reads on YummyAnime: the title,
    the other titles, the numbers on Shikimori and MyAnimeList with their ratings, type, year, genres, episodes, age,
    who voiced it, the text, the poster."""
    name = release.get("name") if isinstance(release.get("name"), dict) else {}
    main = str(name.get("main") or name.get("english") or release.get("alias") or "")
    others = [str(n) for n in (name.get("english"), name.get("alternative")) if n and str(n) != main]
    kind = release.get("type") if isinstance(release.get("type"), dict) else {}
    season = release.get("season") if isinstance(release.get("season"), dict) else {}
    age = release.get("age_rating") if isinstance(release.get("age_rating"), dict) else {}
    genres = [str(g.get("name")) for g in release.get("genres") or [] if isinstance(g, dict) and g.get("name")]
    voices = [str(m.get("nickname")) for m in release.get("members") or []
              if isinstance(m, dict) and m.get("nickname") and isinstance(m.get("role"), dict)
              and m["role"].get("value") == "voicing"]
    year = " ".join(b for b in (str(season.get("description") or ""), str(_year(release) or "")) if b)
    total = release.get("episodes_total")
    rows = [("Тип:", str(kind.get("description") or ""), None), ("Год выпуска:", year, None),
            ("Жанры:", "", genres), ("Эпизоды:", str(total) if isinstance(total, int) and total > 0 else "", None),
            ("Возрастной рейтинг:", str(age.get("label") or ""), None), ("Озвучка:", "", voices)]
    refs = []
    for key, label in (("shikimori", "Shikimori"), ("mal", "MyAnimeList")):
        part = release.get(key) if isinstance(release.get(key), dict) else {}
        if part.get("url") and part.get("id"):
            refs.append((label, part["url"], part.get("rating")))
    poster = release.get("poster") if isinstance(release.get("poster"), dict) else {}
    optimized = poster.get("optimized") if isinstance(poster.get("optimized"), dict) else {}
    picture = _absolute(poster.get("src") or poster.get("preview") or optimized.get("src"), site)
    return title_page(main=main, others=others, refs=refs, rows=rows, picture=picture,
                      text=str(release.get("description") or ""), url=release_url(release, site), origin=f"{NAME} (API)")


# ── a page → what to download ─────────────────────────────────────────────────────
def _check_release(release: dict) -> None:
    what = describe(release)
    if release.get("is_blocked_by_copyrights"):
        raise SourceError(f"{NAME} убрал {what} по требованию правообладателя — скачать его оттуда нельзя")
    if release.get("is_blocked_by_geo"):
        raise SourceError(f"{NAME} закрыл {what} для вашей страны — программа такое не обходит")
    if not [e for e in release.get("episodes") or [] if isinstance(e, dict)]:
        raise SourceError(f"У {NAME} серий {what} пока нет"
                          + (" — тайтл ещё озвучивается" if release.get("is_in_production") else ""))


def _found(page, meta: dict, release: dict, site: str, how: str, root: Path, *, exact: bool, about: str, say,
           stop) -> video_download.Found:
    """The episodes of the release as the list of «Скачать с сайта», into the folder of the page's title (``exact`` —
    the release is the title by its number; ``about`` — whose description: "page" — the page's, "site" — the
    source's)."""
    files = episode_files(release, site)
    title = str(meta.get("title") or "").strip() or (names_of(release) or [""])[0]
    folder, new = video_download.target_folder(root, title, page.url, ids=meta.get("ids"))
    count = sum(1 for f in files if not f.blocked)
    total = release.get("episodes_total")
    ongoing = bool(release.get("is_ongoing"))
    say(f"Серий у {NAME}: {count}" + (f" из {total}" if isinstance(total, int) and total > count else "")
        + (" — тайтл ещё выходит: новые серии докачаются следующим «Найти видео» → «Скачать»" if ongoing else ""))
    source = {"site": NAME, "dub": DUB, "title": (names_of(release) or [""])[0], "what": describe(release),
              "how": how, "exact": exact, "about": about, "episodes": count,
              "total": total if isinstance(total, int) else 0, "ongoing": ongoing}
    voices = []
    if about == "page":                              # the voice-overs the page lists: which one is downloadable
        for name in meta.get("voice") or []:
            name = str(name).strip()
            if name and all(v["name"] != name for v in voices):
                voices.append({"name": name, "here": any(mark in norm(name) for mark in OWN_NAMES)})
    found = video_download.Found(page=page, title=title, folder=folder, files=files, new_folder=new, about=page,
                                 sources=[source], voices=voices, facts=video_download.facts_of(meta))
    return video_download.complete(found, say, stop)


def _how_words(how: str, title: Title, release: dict) -> str:
    theirs = ids_of(release)
    key = next((k for k in ("shikimori", "mal") if k in title.ids and theirs.get(k) == title.ids[k]), "")
    if how == "same":
        return "тот же тайтл: совпал номер " + (f"{'Shikimori' if key == 'shikimori' else 'MyAnimeList'} "
                                                f"{title.ids[key]}" if key else "Shikimori или MyAnimeList")
    if how == "name":
        return ("найден по названию" + (" и году" if title.year else "") + ": номеров Shikimori и MyAnimeList сверить не с "
                "чем — проверьте, тот ли это тайтл")
    return f"страница тайтла на {NAME}"


def find(page, root: Path, *, settings: dict, say, stop_check=None) -> video_download.Found | None:
    """The episodes of the page's title on AniLiberty — None when the page names no title to look for (or the search is
    switched off); raises :class:`SourceError` with the words why they cannot be had."""
    stop = stop_check or (lambda: False)
    if not enabled(settings):
        return None
    alias = release_alias(page.url, site_address((settings or {}).get("aniliberty_url")))
    if alias:                                        # a saved page of AniLiberty itself
        return _by_alias(AniLiberty(site_of(settings, say)), alias, root, say=say, stop=stop)
    meta = video_meta.parse_page(page.html, page.url)
    title = title_of(meta)
    if not title.names or not (title.ids or video_download.is_frame_site(page.url, page.html)):
        return None
    site = site_of(settings, say)
    api = AniLiberty(site)
    numbers = ", ".join(f"{'Shikimori' if k == 'shikimori' else 'MyAnimeList'} {v}" for k, v in title.ids.items())
    say(f"Своего видео на странице нет — ищу этот тайтл на {NAME}" + (f" ({numbers})" if numbers else
                                                                     " по названию и году") + "…")
    found, how, others = find_release(api, title, say=say, stop=stop)
    if stop():
        raise SourceError("Поиск остановлен")
    if found is None:
        tried = ", ".join(f"«{q}»" for q in queries(title)[:3]) or "—"
        raise SourceError(f"На {NAME} этого тайтла нет: поиск по названиям {tried} его не нашёл"
                          + (f" (с похожим названием — другие тайтлы: {', '.join(others[:2])})" if others else ""))
    release = api.release(found.get("id")) or {}
    if not release:
        raise SourceError(f"{NAME} не отдал описание релиза {describe(found)}")
    words = _how_words(how, title, found)
    say(f"{NAME}: {describe(release)} — {words}")
    _check_release(release)
    return _found(page, meta, release, site, words, root, exact=how == "same", about="page", say=say, stop=stop)


def find_alias(address: str, root: Path, *, settings: dict, say, stop_check=None) -> video_download.Found | None:
    """The episodes of an AniLiberty release by the address of its page (its own page is not read: it is a program
    of the browser) — None when the address is not such a page or the source is switched off."""
    if not address or not enabled(settings):
        return None
    site = site_address((settings or {}).get("aniliberty_url"))
    alias = release_alias(address, site)
    if alias:
        site = site_of(settings, say)
    if not alias:
        return None
    return _by_alias(AniLiberty(site), alias, root, say=say, stop=stop_check or (lambda: False))


def _by_alias(api: AniLiberty, alias: str, root: Path, *, say, stop) -> video_download.Found:
    say(f"Страница релиза {NAME} — беру его серии и описание из API сайта")
    release = api.release(alias)
    if not release:
        raise SourceError(f"У {NAME} нет релиза «{alias}» — проверьте ссылку")
    _check_release(release)
    page = release_page(release, api.site)
    meta = video_meta.parse_page(page.html, page.url)
    say(f"{NAME}: {describe(release)}")
    return _found(page, meta, release, api.site, _how_words("", Title(), release), root, exact=True, about="site",
                  say=say, stop=stop)
