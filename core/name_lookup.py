"""(v5.44.162) A site's name the computer does not find is asked once more — of a public resolver, over HTTPS.

The owner (12.11.2026): «nhentai походу не грузит с ленты».  His computer that day went out through a VPN in its
tunnel mode over a phone's internet; names were answered by the operator's resolver, and of «nhentai.net» it said «no
such name» — after eleven seconds (Windows asks every adapter).  The site itself was there: asked by its address it
answered at once, its picture servers had their names.  A browser does not notice such a resolver (it asks the names
over HTTPS itself); the program said «сайт молчит» and kept silent for three minutes.

So a name the system does not find gets a second opinion (:func:`learn`): Cloudflare's and Google's resolvers, asked
by their addresses over HTTPS (their certificates name the addresses — no name is needed to reach them).  An answer is
kept (:data:`KEEP`) and from then on ``socket.getaddrinfo`` of this process gives it for that name (:func:`_hook`) —
whoever asks: the feed, a title's page, the download, a cover.  The connection is the usual one after that: the
site's certificate is checked against its name as always, only the address came another way.  The system is not asked
for that name again in this run (its «no» took eleven seconds each time); when the kept answer is old, the resolvers
are asked anew, and only if they do not answer — the system.

What is never asked of them: a name that cannot be a public site (:func:`askable` — one word, an address, the names
kept for tests and home networks), any name while the fallback is off (``LOCAL_BOORU_NAME_LOOKUP=0`` — the checks),
and a name the system does find.  Who calls :func:`learn`: the code that has just failed to reach a site because its
name was not found (:func:`not_found`) and has no other way to it — ``manhwa_sources.Net`` (after the system's proxy,
if there is one), ``video_meta_import._get``, the pages of a gallery (``manga_download._ask``).

The names reached this way are remembered for the next runs (:data:`REMEMBER`, ``cache/site_names.json`` — names
alone): before a request to one of them (:func:`expect`) the resolvers are asked at once, and the first page of the
run does not wait eleven seconds for the system's «no» either.  When the days are over the system is asked again — it
may know the name by then.  With no internet nobody waits for the resolvers name after name: one that could not be
reached is not asked again for :data:`DOWN_FOR` seconds, whatever the name.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import threading
import time

_LOG = logging.getLogger("local_booru.name_lookup")

# (the resolver's address, the path of its JSON answers): their certificates are given for these addresses
RESOLVERS = (("1.1.1.1", "/dns-query"), ("8.8.8.8", "/resolve"))
ASK_TIMEOUT = (3.0, 4.0)                    # seconds: to connect, to read
KEEP = 30 * 60                              # seconds an answer is used before the resolvers are asked anew
REMEMBER = 3 * 24 * 3600                    # seconds a name the system did not find is asked of the resolvers first
NOT_AGAIN = 2 * 60                          # seconds a name the resolvers have no address for is not asked again
DOWN_FOR = 60                               # seconds nobody is asked after no resolver could be reached
KEPT = 400                                  # names remembered at most
# names that are never a public site: reserved for tests and examples, home and office networks
_NEVER = (".test", ".example", ".invalid", ".localhost", ".local", ".lan", ".home", ".internal", ".intranet",
          ".corp", ".arpa", ".onion", ".ts.net")

# what the program says of a site whose name neither the computer nor the public resolvers have
WORDS = ("компьютер не нашёл адрес сайта — так бывает без интернета и когда сайт закрыт у провайдера, а VPN выключен "
         "или этого сайта не знает")

_LOCK = threading.Lock()
_ASKING = threading.Lock()                  # one question at a time: the others find the answer kept
_KNOWN: dict = {}                           # name → (asked anew after — monotonic, [addresses])
_NOT: dict = {}                             # name → not asked again before (monotonic)
_REAL = [None]                              # the system's own getaddrinfo while the hook stands in its place
_PAST: dict = {"read": False, "names": {}}  # names: name → when the system last did not find it (time.time())
_PAST_LOCK = threading.Lock()               # (the file of the runs before is read once, the others wait for it)
_DOWN = [0.0]                               # the resolvers were not reached: not asked before (monotonic)


def enabled() -> bool:
    return os.environ.get("LOCAL_BOORU_NAME_LOOKUP", "").strip().lower() not in ("0", "off", "false", "no")


def _clean(host) -> str:
    return host.strip().rstrip(".").lower() if isinstance(host, str) else ""


def askable(host: str) -> bool:
    """A name that can be a public site's: with a dot, of letters, digits and hyphens, not an address, not one of the
    names kept for tests and private networks."""
    name = _clean(host)
    if not name or len(name) > 253 or "." not in name or name.endswith(_NEVER):
        return False
    try:
        ipaddress.ip_address(name)
        return False
    except ValueError:
        pass
    labels = name.split(".")
    if labels[-1].isdigit():
        return False
    return all(label and len(label) <= 63 and not label.startswith("-") and not label.endswith("-")
               and all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in label) for label in labels)


def not_found(exc) -> bool:
    """The failure of a request is «the name was not found»: the system's ``gaierror`` anywhere in what caused it
    (``requests`` wraps it three times over), else its words in the text."""
    seen, queue = set(), [exc]
    while queue and len(seen) < 24:
        item = queue.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, socket.gaierror):
            return True
        for more in (getattr(item, "__cause__", None), getattr(item, "__context__", None),
                     getattr(item, "reason", None), *[a for a in getattr(item, "args", ()) or ()
                                                      if isinstance(a, BaseException)]):
            if isinstance(more, BaseException):
                queue.append(more)
    text = str(exc).lower()
    return any(mark in text for mark in ("getaddrinfo failed", "nameresolutionerror", "name or service not known",
                                         "failed to resolve", "nodename nor servname",
                                         "no address associated with hostname",
                                         "temporary failure in name resolution"))


def addresses_of(body) -> "list | None":
    """The IPv4 addresses of a resolver's JSON answer; [] — it says the name has none; None — not an answer."""
    try:
        data = json.loads(body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("Status"), int):
        return None
    out = []
    for row in data.get("Answer") or []:
        if not isinstance(row, dict) or row.get("type") != 1:
            continue
        try:
            address = ipaddress.IPv4Address(str(row.get("data") or "").strip())
        except ValueError:
            continue
        if address.is_global and str(address) not in out:      # («0.0.0.0» and the like — a filter's «no»)
            out.append(str(address))
    return out[:8]


def _over_https(name: str) -> "list | None":
    """The addresses the public resolvers give for the name; [] — a resolver answered that it has none; None — no
    resolver was reached (no internet, or they are closed here)."""
    import requests
    for address, path in RESOLVERS:
        try:
            with requests.Session() as session:
                session.trust_env = False                      # straight: the resolver is asked by its address
                answer = session.get(f"https://{address}{path}", params={"name": name, "type": "A"},
                                     headers={"Accept": "application/dns-json"}, timeout=ASK_TIMEOUT)
                body = answer.content[:65536] if answer.status_code == 200 else b""
        except requests.RequestException as exc:
            _LOG.info("name lookup: the resolver %s did not answer (%s)", address, type(exc).__name__)
            continue
        found = addresses_of(body)
        if found is not None:
            return found
    return None


ASK = _over_https                           # (a check puts its own)


def _hook(host, port, family=0, type=0, proto=0, flags=0):
    """``socket.getaddrinfo`` of this process: a name learned — by its kept addresses; anything else — the system's."""
    real = _REAL[0] or socket.getaddrinfo
    name = _clean(host)
    if name and family in (0, socket.AF_INET) and type in (0, socket.SOCK_STREAM):
        out = []
        for address in _kept(name):
            out += real(address, port, family, type, proto, flags)
        if out:
            return out
    return real(host, port, family, type, proto, flags)


def _kept(name: str) -> list:
    """The addresses kept for a learned name; an old answer is asked anew first (not there any more — [] and the name
    is the system's again)."""
    with _LOCK:
        found = _KNOWN.get(name)
    if found is None:
        return []
    if time.monotonic() < found[0]:
        return found[1]
    with _ASKING:
        with _LOCK:
            found = _KNOWN.get(name)
        if found is None:
            return []
        if time.monotonic() < found[0]:
            return found[1]
        if not enabled():
            fresh = []
        elif time.monotonic() < _DOWN[0]:
            fresh = None                                       # (the resolvers were not reached a moment ago)
        else:
            fresh = ASK(name)
        with _LOCK:
            if fresh:
                _KNOWN[name] = (time.monotonic() + KEEP, list(fresh))
            elif fresh is None:                                # nobody reached: the old answer a while longer
                _DOWN[0] = max(_DOWN[0], time.monotonic() + DOWN_FOR)
                _KNOWN[name] = (time.monotonic() + KEEP / 3, found[1])
                fresh = found[1]
            else:
                _KNOWN.pop(name, None)
        return list(fresh or [])


def _install() -> None:
    if _REAL[0] is None:
        _REAL[0] = socket.getaddrinfo
        socket.getaddrinfo = _hook


def _file():
    from core.paths import CACHE_DIR
    return CACHE_DIR / "site_names.json"


def _past() -> dict:
    """{name: when the system did not find it} — of this run and of the days before (read once)."""
    if _PAST["read"]:
        return _PAST["names"]
    with _PAST_LOCK:
        if _PAST["read"]:
            return _PAST["names"]
        names = {}
        try:
            from core.file_safety import read_json_kept
            kept = read_json_kept(_file(), dict, what="site names") or {}
            now = time.time()
            for name, when in (kept.get("names") or {}).items():
                if isinstance(name, str) and isinstance(when, (int, float)) and 0 <= now - when < REMEMBER \
                        and askable(name):
                    names[_clean(name)] = float(when)
        except (OSError, ValueError, AttributeError) as exc:
            _LOG.info("name lookup: the names of the runs before not read (%s)", exc)
        with _LOCK:
            _PAST["names"] = names
            _PAST["read"] = True
        return names


def _note(name: str) -> None:
    """The system has just not found ``name`` and the resolvers gave it: remembered for the next runs."""
    names = _past()
    now = time.time()
    with _LOCK:
        if now - names.get(name, 0.0) < 3600:                  # (written a moment ago: several requests failed at once)
            return
        names[name] = now
        for old in [n for n, when in names.items() if now - when >= REMEMBER]:
            names.pop(old, None)
        while len(names) > KEPT:
            names.pop(min(names, key=names.get))
        text = json.dumps({"names": dict(names)}, ensure_ascii=False, indent=1)
    try:
        from core.file_safety import write_json_kept
        write_json_kept(_file(), text, dict)
    except OSError as exc:
        _LOG.info("name lookup: the name not remembered for the next run (%s)", exc)


def _proxied(url: str) -> bool:
    """The system has a proxy for this address (``requests`` would go through it)."""
    try:
        import requests
        return bool(requests.utils.select_proxy(url, requests.utils.get_environ_proxies(url)))
    except (OSError, ValueError):
        return False


def expect(url: str) -> None:
    """Before a request to ``url``: if its site's name is one the system did not find lately, the resolvers are asked
    now (once a run) — the system's «no» is not waited for again.  Not while the system has a proxy for it: the
    request goes through the proxy, or will after it fails straight."""
    from urllib.parse import urlparse
    name = _clean(urlparse(str(url or "")).hostname or "")
    if not name or not enabled():
        return
    with _LOCK:
        if name in _KNOWN or time.monotonic() < max(_NOT.get(name, 0.0), _DOWN[0]):
            return
    if name not in _past():                  # (the usual case: one look into a dictionary)
        return
    if _proxied(str(url)):
        return
    learn(name, noted=False)


def learn(host: str, noted: bool = True) -> bool:
    """The system did not find ``host``: ask the public resolvers.  True — they gave its addresses, and from now on
    this process reaches it by them (the request can be made again at once).  ``noted`` — the system has just said
    its «no» (remembered for the next runs); False — asked ahead of it (:func:`expect`)."""
    name = _clean(host)
    if not enabled() or not askable(name):
        return False

    def settled() -> "bool | None":
        """True — its addresses are kept; False — not asked now; None — to be asked."""
        now = time.monotonic()
        found = _KNOWN.get(name)
        if found is not None and now < found[0]:
            return True
        if now < _NOT.get(name, 0.0) or now < _DOWN[0]:
            return False
        return None

    with _LOCK:
        known = settled()
    if known is None:
        with _ASKING:
            with _LOCK:
                known = settled()                              # (another thread has just asked)
            if known is None:
                addresses = ASK(name)
                with _LOCK:
                    if addresses:
                        while len(_KNOWN) >= KEPT:
                            _KNOWN.pop(next(iter(_KNOWN)))
                        _KNOWN[name] = (time.monotonic() + KEEP, list(addresses))
                        _NOT.pop(name, None)
                        _install()
                    elif addresses is None:
                        _DOWN[0] = time.monotonic() + DOWN_FOR
                    else:
                        while len(_NOT) >= KEPT:
                            _NOT.pop(next(iter(_NOT)))
                        _NOT[name] = time.monotonic() + NOT_AGAIN
                known = bool(addresses)
                if addresses:
                    _LOG.info("name lookup: the system does not find %s — reached by the address a public resolver "
                              "gave", name)
                elif addresses is None:
                    _LOG.info("name lookup: the system does not find %s, and no public resolver was reached", name)
                else:
                    _LOG.info("name lookup: the system does not find %s, and the public resolvers have no address "
                              "for it", name)
    if known and noted:
        _note(name)                                            # the next runs ask the resolvers at once
    return bool(known)


def retry_for(exc, url: str) -> bool:
    """A request to ``url`` failed with ``exc``: True — its site's name was not found, a public resolver has just
    given it, ask again."""
    if not not_found(exc):
        return False
    from urllib.parse import urlparse
    return learn(urlparse(str(url or "")).hostname or "")


def known() -> dict:
    """{name: [addresses]} learned in this run («Диагностика», a check)."""
    with _LOCK:
        return {name: list(found[1]) for name, found in _KNOWN.items()}


def forget() -> None:
    """Everything learned is forgotten and the system's own lookup is back in its place (a check)."""
    with _LOCK:
        _KNOWN.clear()
        _NOT.clear()
        _PAST["names"], _PAST["read"] = {}, False
        _DOWN[0] = 0.0
        if _REAL[0] is not None:
            if socket.getaddrinfo is _hook:
                socket.getaddrinfo = _REAL[0]
            _REAL[0] = None
