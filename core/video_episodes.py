"""The pages of a series' episodes («Скачать с сайта»): a site that shows each episode on a page of its own — the page of
the series only links to them (hentaihaven: ``/watch/<series>/episode-1/``, ``…/episode-2/``).

* :func:`links` — the episode pages a page links to: addresses one level under the series' own address whose last
  part names an episode (``episode-2``, ``ep2``, ``seriya-3``, ``2``), in the order of their numbers; the page of an
  episode finds its sister episodes the same way (their series is the address one level up);
* :func:`series_url` — the series' page of an episode's page ("" — the page is not an episode's);
* :func:`episode_name` — a file's name from an episode page's title, without the series' title before it
  («Mugoku no Kuni no Alice — Эпизод 2» → «Эпизод 2»).
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlunparse

MAX_EPISODES = 60               # episode pages read for one series at most
_EPISODE = re.compile(r"^(?:(?:episode|ep|e|seriya|seria|serija|series|серия|эпизод)[-_ ]?)?(\d{1,4})$", re.I)


def _parts(url: str) -> tuple[str, list[str]]:
    parsed = urlparse(str(url or ""))
    return (parsed.hostname or "").removeprefix("www."), [p for p in parsed.path.split("/") if p]


def episode_number(url: str) -> int | None:
    """The episode an address names in its last part (``…/episode-2/`` → 2), else None."""
    _host, parts = _parts(url)
    found = _EPISODE.match(parts[-1]) if parts else None
    return int(found.group(1)) if found else None


def series_url(url: str) -> str:
    """The address one level up when this one is an episode's page, else ""."""
    if episode_number(url) is None:
        return ""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, "/" + "/".join(parts[:-1]) + "/", "", "", ""))


def links(soup, page_url: str) -> list[tuple[int, str]]:
    """(number, address) of the episode pages of this page's series, in the order of their numbers."""
    host, own = _parts(page_url)
    if not host or not own:
        return []
    series = own[:-1] if episode_number(page_url) is not None else own
    if not series:
        return []
    found: dict[int, str] = {}
    for a in soup.find_all("a", href=True):
        address = urljoin(page_url, str(a["href"]).strip())
        if not address.startswith(("http://", "https://")):
            continue
        a_host, parts = _parts(address)
        if a_host != host or len(parts) != len(series) + 1 or parts[:-1] != series:
            continue
        number = episode_number(address)
        if number is not None and number not in found:
            parsed = urlparse(address)
            found[number] = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))
    return sorted(found.items())[:MAX_EPISODES]


def episode_name(title: str, series_title: str, number: int | None) -> str:
    """A file's name for an episode: its page's title without the series' title before it; «Серия N» when the page
    says nothing more."""
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    series = re.sub(r"\s+", " ", str(series_title or "")).strip()
    if series and text.lower().startswith(series.lower()):
        text = text[len(series):].strip(" —–-:|·.")
    if not text or text.lower() == series.lower():
        text = f"Серия {number}" if number is not None else ""
    return text
