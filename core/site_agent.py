"""Who the program says it is to the sites whose rules forbid a program to say it is a browser (v5.44.22, v5.44.24).

e621 (its API help, https://e621.net/help/api — read 1.11.2026):

* «A non-empty User-Agent header is required for all requests … You should include your e621 username, so that you
  may be contacted if your project causes problems.  For example: ``MyProject/1.0 (by username on e621)``»;
* «Do not impersonate a browser user agent, as this will get you blocked»;
* a request made inside a browser (an extension cannot set the header) carries the same words in the ``_client``
  parameter of its address.

Danbooru (its help:api — read 1.11.2026): «Clients should identify themselves with a unique User-Agent header that
contains your user ID, for example ``YourBotName/1.0 (user #id)`` … Don't impersonate browsers or use the default
header of your library»; «Badly-behaved bots will be banned swiftly»; about one request a second over a longer session.

Before, the parser's previews and the downloads of these sites' pictures (static1.e621.net, cdn.donmai.us) went with a
browser's User-Agent (core/parser/http.py ``BROWSER_UA``, core/downloader_utils.py ``_UA``), a Companion handoff of
their cookies put the browser's own in its place, the API's words were different in every part («LocalBooru/3.2»,
«3.6», «4.0») and never had Danbooru's user number.  Now every request to their hosts says :func:`user_agent`: the
program's name and the person's account from «Сайты и ключи» — the login (never the API key) and, for Danbooru, the
user number: its «User ID», else the one Danbooru's ``/profile.json`` tells once per start of the program
(``core/parser/sites/danbooru.py``).  A browser's User-Agent given for them (in a site's settings, by a cookie handoff)
is not used.  Other sites keep theirs (their rules ask nothing of it: rule34.xxx, Gelbooru; ATF's page of rules shows a
check «I am not a robot» to a program, so it was not read).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

VERSION = "5.44"
PRODUCT = f"LocalBooru/{VERSION}"
_BROWSER_WORDS = ("mozilla/", "applewebkit", "chrome/", "safari/", "firefox/", "gecko/", "edg/", "opera")


@dataclass(frozen=True)
class Family:
    key: str            # "e621", "danbooru"
    label: str          # the site's name in the words: «… on e621», «… on Danbooru»
    domains: tuple      # its hosts: these and their subdomains
    accounts: tuple     # rows of «Сайты и ключи» whose login (and number) its requests carry
    numbered: bool      # its rules ask for the user's number


FAMILIES = (
    Family("e621", "e621", ("e621.net", "e926.net"), ("e621.net", "e926.net"), False),
    Family("danbooru", "Danbooru", ("donmai.us",), ("danbooru.donmai.us", "donmai.us", "safebooru.donmai.us"), True),
)

_LOCK = threading.Lock()
_LEARNED: dict = {}          # (family key, login) -> the user's number a site told (for this start of the program)
_TRIED: set = set()


def _host(host_or_url) -> str:
    raw = str(host_or_url or "").strip().lower()
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    raw = raw.split("/")[0].split(":")[0]
    return raw[4:] if raw.startswith("www.") else raw


def family(host_or_url) -> Family | None:
    host = _host(host_or_url)
    for fam in FAMILIES:
        if any(host == d or host.endswith("." + d) for d in fam.domains):
            return fam
    return None


def is_own(host_or_url) -> bool:
    """A host of a site whose rules forbid a browser's User-Agent (e621, Danbooru): it gets the program's own."""
    return family(host_or_url) is not None


def is_e621(host_or_url) -> bool:
    """e621.net, e926.net and their servers (static1.e621.net …)."""
    fam = family(host_or_url)
    return fam is not None and fam.key == "e621"


def _rows(settings) -> dict:
    sites = (settings or {}).get("sites") if isinstance(settings, dict) else None
    return sites if isinstance(sites, dict) else {}


def account(settings, fam: Family) -> tuple:
    """(login, user number) of the person on the site, from «Сайты и ключи» ("" — not written); the number — its
    «User ID», else the one the site told in this start of the program."""
    rows = _rows(settings)
    login = number = ""
    for key in fam.accounts:
        for name in (key, "www." + key):
            cfg = rows.get(name)
            if not isinstance(cfg, dict):
                continue
            login = login or str(cfg.get("login") or "").strip()
            uid = str(cfg.get("user_id") or "").strip()
            number = number or (uid if uid.isdigit() else "")
    if fam.numbered and not number and login:
        number = learned_user_id(fam.key, login)
    return login, number


def remember_user_id(fam_key: str, login: str, number) -> None:
    with _LOCK:
        _LEARNED[(fam_key, str(login or "").strip().lower())] = str(number)


def learned_user_id(fam_key: str, login: str) -> str:
    with _LOCK:
        return _LEARNED.get((fam_key, str(login or "").strip().lower()), "")


def first_try(fam_key: str, login: str) -> bool:
    """True once per start of the program for this account: ask the site for the user's number."""
    key = (fam_key, str(login or "").strip().lower())
    with _LOCK:
        if key in _TRIED:
            return False
        _TRIED.add(key)
        return True


def looks_like_browser(agent) -> bool:
    text = str(agent or "").strip().lower()
    return any(word in text for word in _BROWSER_WORDS)


def user_agent(host_or_url, settings=None, *, name: str | None = None, user_id: str | None = None,
               custom: str = "") -> str:
    """The words of a request to the host: «LocalBooru/5.44 (by <login> on e621)»; Danbooru — «LocalBooru/5.44 (by
    <login> on Danbooru, user #<number>)», «(user #<number>)» without the login; nothing known — «(local archive
    manager)».  ``custom`` (a site's own «user_agent» setting) is taken when it is not a browser's."""
    custom = " ".join(str(custom or "").split())
    if custom and not looks_like_browser(custom):
        return custom
    fam = family(host_or_url)
    if fam is None:
        return f"{PRODUCT} (local archive manager)"
    login, number = account(settings, fam)
    who = " ".join(str(name if name is not None else login).split())
    if fam.numbered:
        number = str(user_id or "").strip() if user_id is not None else number
        if not number and who:
            number = learned_user_id(fam.key, who)
        number = number if number.isdigit() else ""
    else:
        number = ""
    if who and number:
        return f"{PRODUCT} (by {who} on {fam.label}, user #{number})"
    if number:
        return f"{PRODUCT} (user #{number})"
    if who:
        return f"{PRODUCT} (by {who} on {fam.label})"
    return f"{PRODUCT} (local archive manager)"


def fix_headers(host_or_url, headers, settings=None):
    """``headers`` of a request: to such a host a browser's User-Agent in them is replaced with :func:`user_agent`
    (any other host, or no User-Agent in them: unchanged — the session's own goes)."""
    if not headers or not is_own(host_or_url):
        return headers
    out = dict(headers)
    for key in list(out):
        if str(key).lower() == "user-agent" and looks_like_browser(out[key]):
            out[key] = user_agent(host_or_url, settings)
    return out


def with_client(url: str, settings=None, *, name: str | None = None) -> str:
    """The address of a request a browser makes to e621 (it cannot set the User-Agent): with ``_client``."""
    parts = urlsplit(str(url or ""))
    if not is_e621(parts.hostname or ""):
        return str(url or "")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "_client"]
    query.append(("_client", user_agent(parts.hostname, settings, name=name)))
    return urlunsplit(parts._replace(query=urlencode(query)))
