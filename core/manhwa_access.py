"""(v5.44.78) «Адреса сайтов и вход» — how the program reaches the sites of manga and manhwa.

The owner (5.11.2026): «Делай все что я говорил и что там по плану было» — from his friend's reader (Yomi 1.3, read,
never run): «Адреса сайтов (зеркала)» — a site's own domain when it moved; cookies of a site from a file (cookies.txt
of Netscape or the JSON of a browser's cookie extension) and MangaLib's token from its localStorage «auth» — the titles
18+ the sites show only to the signed in.

* The addresses — the settings' ``manga_site_addresses`` ({site: address}, ``<site>_api`` — the API of Remanga,
  ReComics, MangaLib when it is an address of its own), put in place by :func:`apply` (``manhwa_sources.use_addresses``).
* The sign-in — ``manga_logins.json`` in the settings folder ({site: {"cookies": [...], "token", "until", "at"}}),
  never in ``app_settings.json`` (the profile of all settings is a file to carry around); put in place by :func:`apply`
  (``manhwa_sources.use_signins``).  Only a site's own cookies are kept (its domains and the address it is at now);
  an expired one is dropped.  The values never go to a page, a journal or an error: :func:`summary` says how many
  cookies and until when the token lasts.
* (v5.44.100) The sign-in from the owner's browser — the extension «Local Booru Companion» 0.10.0 (the owner,
  6.11.2026: «авто сбор куки с сайтов которые нужны программе через расширение что бы не ебаться с txt / открыл сайт
  куки собрались и с возможностью обновить их через расширение / открыть сайты можно будет в настройках сайты
  манги»): it asks :func:`browser_sites` (each site's domains, the address to open, the localStorage key of its token),
  sends what the browser has of a site when its page loads, its cookies change or the program asks
  (:func:`take_from_browser`), and takes the program's asks — «Открыть сайт», «Обновить вход» of «Сайты манги»
  (:func:`ask_browser` → :func:`browser_asks`).  :func:`browser_state` — whether such an extension is there now.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time

from core import manhwa_sources as S

_LOG = logging.getLogger("local_booru.manhwa")

FILE = "manga_logins.json"
SETTING = "manga_site_addresses"
LANGUAGE_SETTING = "manga_language"   # (v5.44.79) the language of MangaDex's chapters
MAX_TEXT = 2_000_000                  # a file of cookies bigger than this is not one
TOKEN_SITES = ("mangalib",)           # the sites whose API takes the owner's token
# (v5.44.100) the sign-in from the browser (the extension 0.10.0)
BROWSER_TOKEN = {"mangalib": "auth", "hentailib": "auth"}   # the localStorage key of a Lib site's token
BROWSER_VERSION = (0, 10, 0)          # the extension that takes the sign-in of the sites of manga
MAX_COOKIES = 300                     # of one site: more is not a sign-in
SEEN_FOR = 45                         # seconds: the extension asked the program within this — it is there
ASK_TTL = 120                         # an ask the extension has not taken in this long is dropped
BROWSER_SKIP = ("hentaichan.ru",)     # a domain of a site's links that is another site now (6.11.2026): not asked
_LOCK = threading.Lock()


def _path():
    from core.paths import SETTINGS_DIR
    return SETTINGS_DIR / FILE


def load() -> dict:
    from core.file_safety import read_json_kept
    data = read_json_kept(_path(), dict, what="manga sign-in")
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    from core.file_safety import write_json_kept
    write_json_kept(_path(), json.dumps(data, ensure_ascii=False, indent=1), dict)


def apply(settings: dict | None = None) -> None:
    """The addresses and (v5.44.79) the language of the chapters (of ``settings``; None — not changed) and the sign-in
    in place for every request of the sites."""
    if settings is not None:
        S.use_addresses((settings or {}).get(SETTING) or {})
        S.use_language((settings or {}).get(LANGUAGE_SETTING) or "ru")
    S.use_signins(load())


# ── what the page shows ──────────────────────────────────────────────────────────────────────────────────────────
def sites() -> list:
    """Every site of manga the program knows: {name, label, address (its own), mine (the owner's), api — the API when it
    is an address of its own: {default, mine}}."""
    out = []
    for source in S.SOURCES:
        item = {"name": source.name, "label": source.label, "address": S.default_address(source.name),
                "mine": S.address(source.name), "hosts": list(getattr(source, "hosts", ()) or ()),
                "token": source.name in TOKEN_SITES}
        if source.name in S.API_OF:
            item["api"] = {"default": S.default_address(source.name + "_api"), "mine": S.address(source.name + "_api")}
        out.append(item)
    return out


def summary() -> dict:
    """{site: {"cookies": how many, "domains": [...], "token": bool, "until": when the token ends, "at": when given,
    "from": "browser" (the extension) or "file"}} — never a value."""
    out = {}
    now = time.time()
    for name, value in load().items():
        if not isinstance(value, dict):
            continue
        cookies = [c for c in value.get("cookies") or [] if isinstance(c, dict) and not _expired(c, now)]
        out[name] = {"cookies": len(cookies), "domains": sorted({str(c.get("domain") or "").lstrip(".")
                                                                 for c in cookies} - {""}),
                     "token": bool(value.get("token")), "until": int(value.get("until") or 0),
                     "at": int(value.get("at") or 0), "from": str(value.get("from") or "file")}
    return out


# ── cookies ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _expired(cookie: dict, now: float) -> bool:
    try:
        ends = float(cookie.get("expires") or 0)
    except (TypeError, ValueError):
        return False
    return 0 < ends < now


def parse_cookies(text: str) -> list:
    """The cookies of a file: cookies.txt of Netscape (tab-separated: domain, flag, path, secure, expiry, name, value;
    «#HttpOnly_» before the domain) or JSON (a list of {domain, name, value, path, expirationDate}, or {"cookies": […]}
    — Cookie-Editor, EditThisCookie, a browser's own export).  [{domain, name, value, path, expires}]."""
    text = str(text or "").lstrip("﻿").strip()
    if not text:
        return []
    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("Файл похож на JSON, но не читается — сохраните cookies ещё раз") from None
        items = data.get("cookies") if isinstance(data, dict) else data
        out = []
        for c in items if isinstance(items, list) else []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            expires = c.get("expirationDate", c.get("expires", c.get("expiry", 0)))
            out.append({"domain": str(c.get("domain") or c.get("host") or ""), "name": str(c["name"]),
                        "value": str(c.get("value") or ""), "path": str(c.get("path") or "/"),
                        "expires": float(expires) if isinstance(expires, (int, float)) and expires > 0 else 0})
        return out
    out = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, _secure, expires, name, value = parts[:7]
        try:
            ends = float(expires or 0)
        except ValueError:
            ends = 0.0
        if name:
            out.append({"domain": domain.strip(), "name": name, "value": value, "path": path or "/",
                        "expires": ends if ends > 0 else 0})
    return out


def site_hosts(name: str) -> tuple:
    """The domains whose cookies are the site's: its own and the one of the address it is at now."""
    source = S.by_name(name)
    if source is None:
        return ()
    hosts = set(getattr(source, "hosts", ()) or ())
    for address in (source.site(), S.address(name + "_api")):
        from urllib.parse import urlparse
        host = (urlparse(address or "").hostname or "").lower().removeprefix("www.")
        if host:
            hosts.add(host)
    return tuple(sorted(hosts))


def _belongs(domain: str, hosts: tuple) -> bool:
    domain = str(domain or "").lower().lstrip(".")
    return bool(domain) and any(domain == h or domain.endswith("." + h) or h.endswith("." + domain) for h in hosts)


def put_cookies(name: str, text: str) -> dict:
    """The owner's cookies of the site ``name`` from a file's text: only the site's own, not expired.  {"cookies": how
    many kept, "other": how many of other sites left out}; ValueError — words."""
    if S.by_name(name) is None:
        raise ValueError("Такого сайта программа не знает")
    if len(text or "") > MAX_TEXT:
        raise ValueError("Файл слишком большой для cookies")
    every = parse_cookies(text)
    if not every:
        raise ValueError("В файле нет cookies: нужен cookies.txt (Netscape) или JSON расширения для cookies")
    hosts = site_hosts(name)
    now = time.time()
    mine = [c for c in every if _belongs(c["domain"], hosts) and not _expired(c, now)]
    if not mine:
        raise ValueError(f"В файле нет cookies этого сайта ({', '.join(hosts)}) — сохраните их на его странице, "
                         "где вы вошли")
    with _LOCK:
        data = load()
        entry = dict(data.get(name) or {})
        entry.update({"cookies": mine, "at": int(now), "from": "file"})
        data[name] = entry
        _write(data)
    apply()
    _LOG.info("manga sign-in: %d cookies of %s kept (%d of other sites left out)", len(mine), name,
              len(every) - len(mine))
    return {"cookies": len(mine), "other": len(every) - len(mine)}


# ── MangaLib's token ─────────────────────────────────────────────────────────────────────────────────────────────
_JWT = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")


def _jwt_until(token: str) -> int:
    """When a token of the JWT kind ends (its «exp»); 0 — it does not say."""
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return int(data.get("exp") or 0)
    except (IndexError, ValueError, TypeError, AttributeError):
        return 0


def parse_token(text: str) -> tuple:
    """(the token, until when) of what the owner pasted: the value of «auth» of MangaLib's localStorage
    ({"token": {"access_token", "expires_in", "timestamp"}, …}), «Bearer …» or the token itself; ValueError — words."""
    text = str(text or "").strip().strip('"').strip()
    if not text:
        raise ValueError("Вставьте значение «auth» из хранилища сайта или сам токен")
    if len(text) > 100_000:
        raise ValueError("Это слишком длинно для токена")
    token, until = "", 0
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("Значение «auth» не читается — скопируйте его целиком") from None
        holder = data.get("token") if isinstance(data, dict) else None
        if isinstance(holder, dict):
            token = str(holder.get("access_token") or "")
            try:
                stamp = float(holder.get("timestamp") or 0)
                stamp = stamp / 1000 if stamp > 1e11 else stamp          # milliseconds or seconds
                until = int(stamp + float(holder.get("expires_in") or 0)) if stamp else 0
            except (TypeError, ValueError):
                until = 0
        elif isinstance(holder, str):
            token = holder
        elif isinstance(data, dict):
            token = str(data.get("access_token") or "")
    else:
        token = re.sub(r"(?i)^bearer\s+", "", text).strip()
    if not token or re.search(r"\s", token) or len(token) < 20:
        raise ValueError("Токена здесь нет: нужно значение «auth» целиком (оно начинается с {) или сам токен")
    return token, until or _jwt_until(token)


def put_token(name: str, text: str) -> dict:
    """The owner's token of the site ``name`` (MangaLib); {"until": when it ends (0 — not known)}."""
    if name not in TOKEN_SITES:
        raise ValueError("Этому сайту токен не нужен — только cookies")
    token, until = parse_token(text)
    if until and until < time.time():
        raise ValueError("Этот токен уже закончился — войдите на сайт заново и скопируйте «auth» ещё раз")
    with _LOCK:
        data = load()
        entry = dict(data.get(name) or {})
        entry.update({"token": token, "until": until, "at": int(time.time()), "from": "file"})
        data[name] = entry
        _write(data)
    apply()
    _LOG.info("manga sign-in: the token of %s kept", name)
    return {"until": until}


def forget(name: str, what: str = "all") -> None:
    """The site's cookies ("cookies"), its token ("token") or both ("all") taken away."""
    with _LOCK:
        data = load()
        entry = dict(data.get(name) or {})
        if what in ("cookies", "all"):
            entry.pop("cookies", None)
        if what in ("token", "all"):
            entry.pop("token", None)
            entry.pop("until", None)
        if entry.get("cookies") or entry.get("token"):
            data[name] = entry
        else:
            data.pop(name, None)
        _write(data)
    apply()


# ── (v5.44.100) the sign-in from the owner's browser: the extension «Local Booru Companion» 0.10.0 ────────────────
_BROWSER = {"seen": 0.0, "any": 0.0, "version": ""}   # when the extension 0.10 asked last; when any extension did
_ASKS: list = []                                       # [{"id", "site", "action", "at"}] the extension takes
_ANSWERS: dict = {}                                    # site → what the extension sent last (no value)
_ASKED: dict = {}                                      # an ask's id → the extension's answer to it (no value)
ASKED_KEPT = 50
_ASK_COUNT = [0]


def _version(text: str) -> tuple:
    try:
        return tuple(int(x) for x in str(text or "").split(".")[:3])
    except ValueError:
        return ()


def browser_seen(version: str = "", *, manga: bool = False) -> None:
    """A paired extension asked the program (``manga`` — about the sites of manga: it can take their sign-in)."""
    now = time.time()
    with _LOCK:
        _BROWSER["any"] = now
        if manga and _version(version) >= BROWSER_VERSION:
            _BROWSER["seen"] = now
            _BROWSER["version"] = str(version)[:20]


def browser_state() -> dict:
    """{"connected": the extension 0.10 is there (asked within :data:`SEEN_FOR`), "old": an extension is there that
    does not take the sign-in (an old one: reloaded on the browser's page of extensions — it does), "version"}."""
    now = time.time()
    connected = now - _BROWSER["seen"] <= SEEN_FOR
    return {"connected": connected, "old": not connected and now - _BROWSER["any"] <= SEEN_FOR,
            "version": _BROWSER["version"] if connected else ""}


def browser_sites() -> list:
    """For the extension: every site of manga — its name, its label, the domains whose cookies are its
    (:func:`site_hosts`), the address its «Открыть сайт» opens, the localStorage key of its token (Lib sites), whether
    the program keeps a sign-in of it (``kept``: none — the extension sends what it has again, not «already sent»)."""
    out = []
    for source in S.SOURCES:
        domains = sorted({h.removeprefix("www.") for h in site_hosts(source.name)} - set(BROWSER_SKIP))
        kept = S._SIGNINS.get(source.name) or {}
        out.append({"site": source.name, "label": source.label, "domains": domains,
                    "open": source.site().rstrip("/") + "/", "token": BROWSER_TOKEN.get(source.name, ""),
                    "kept": bool(kept.get("cookies") or kept.get("token"))})
    return out


def browser_rev() -> str:
    """What changes the long poll of the extension (``/extension/events/wait``): an ask; a new address of a site, a
    sign-in the program no longer keeps («Убрать»)."""
    import hashlib
    sites = json.dumps([(x["site"], x["domains"], x["open"], x["kept"]) for x in browser_sites()], ensure_ascii=False)
    return f"{_ASK_COUNT[0]}:{hashlib.sha1(sites.encode('utf-8')).hexdigest()[:12]}"


def ask_browser(name: str, action: str) -> dict:
    """«Открыть сайт» / «Обновить вход» of «Сайты манги»: an ask for the extension (it takes them —
    :func:`browser_asks`).  {"queued": False} — no extension 0.10 is there to take it."""
    if S.by_name(name) is None or action not in ("open", "refresh"):
        raise ValueError("Такого сайта программа не знает")
    if not browser_state()["connected"]:
        return {"queued": False}
    now = time.time()
    with _LOCK:
        _ASK_COUNT[0] += 1
        _ASKS[:] = [a for a in _ASKS if now - a["at"] < ASK_TTL and (a["site"], a["action"]) != (name, action)]
        _ASKS.append({"id": _ASK_COUNT[0], "site": name, "action": action, "at": now})
    return {"queued": True, "id": _ASK_COUNT[0]}


def browser_asks() -> list:
    """The asks the extension has not taken yet (taken now): [{"id", "site", "action"}]."""
    now = time.time()
    with _LOCK:
        fresh = [a for a in _ASKS if now - a["at"] < ASK_TTL]
        _ASKS.clear()
    return [{"id": a["id"], "site": a["site"], "action": a["action"]} for a in fresh]


def browser_answers() -> dict:
    """site → what the extension sent last: {"at", "cookies" (how many kept), "token", "missing" — domains the
    extension may not read (the browser did not let it), "asked" — the id of the ask it answered} — no value."""
    with _LOCK:
        return {k: dict(v) for k, v in _ANSWERS.items()}


def asked_answers() -> dict:
    """An ask's id (a string) → the extension's answer to it (as :func:`browser_answers`): the page waits for its own —
    a later send of the same site (its cookies changed) does not hide it."""
    with _LOCK:
        return {str(k): dict(v) for k, v in _ASKED.items()}


def _browser_cookie(c: dict) -> "dict | None":
    """A cookie as the browser's extension API gives it ({domain, name, value, path, expirationDate, session…}) — as
    the program keeps them; a wrong one — None."""
    if not isinstance(c, dict):
        return None
    name, value = c.get("name"), c.get("value", "")
    if not isinstance(name, str) or not name or len(name) > 256 or not isinstance(value, str) or len(value) > 8192:
        return None
    ends = c.get("expirationDate", c.get("expires", 0))
    ends = float(ends) if isinstance(ends, (int, float)) and not c.get("session") and ends > 0 else 0
    return {"domain": str(c.get("domain") or "")[:255], "name": name, "value": value,
            "path": str(c.get("path") or "/")[:1024], "expires": ends}


def take_from_browser(name: str, cookies, token: str = "", *, missing=(), asked: int = 0) -> dict:
    """What the extension took in the owner's browser for the site ``name``: its cookies (only the site's domains, not
    expired) take the place of the kept ones — none: the kept stay (the browser has nothing of it); the token of a Lib
    site (its localStorage «auth»), when there is one and it has not ended.  {"cookies": how many kept, "token": bool,
    "changed": bool} — never a value; ValueError — words."""
    if S.by_name(name) is None:
        raise ValueError("Такого сайта программа не знает")
    every = [c for c in (_browser_cookie(x) for x in (cookies if isinstance(cookies, list) else [])[:2000]) if c]
    hosts = site_hosts(name)
    now = time.time()
    mine = [c for c in every if _belongs(c["domain"], hosts) and not _expired(c, now)][:MAX_COOKIES]
    got_token, until = "", 0
    if token and name in BROWSER_TOKEN:
        try:
            got_token, until = parse_token(str(token)[:100_000])
        except ValueError:
            got_token = ""
        if until and until < now:
            got_token = ""                            # ended: the site's page has not refreshed it yet
    changed = False
    with _LOCK:
        data = load()
        entry = dict(data.get(name) or {})
        if mine and _key(mine) != _key(entry.get("cookies") or []):
            entry["cookies"] = mine
            changed = True
        if got_token and got_token != entry.get("token"):
            entry.update({"token": got_token, "until": until})
            changed = True
        if changed:
            entry.update({"at": int(now), "from": "browser"})
            data[name] = entry
            _write(data)
        _ANSWERS[name] = {"at": int(now), "cookies": len(mine), "token": bool(got_token),
                          "missing": [str(d)[:100] for d in list(missing or ())[:20]], "asked": int(asked or 0)}
        if asked:
            _ASKED[int(asked)] = dict(_ANSWERS[name], site=name)
            while len(_ASKED) > ASKED_KEPT:
                _ASKED.pop(next(iter(_ASKED)))
    if changed:
        apply()
        _LOG.info("manga sign-in from the browser: %d cookies of %s%s", len(mine), name,
                  ", the token" if got_token else "")
    return {"cookies": len(mine), "token": bool(got_token), "changed": changed}


def _key(cookies: list) -> list:
    return sorted((str(c.get("domain")), str(c.get("path")), str(c.get("name")), str(c.get("value")))
                  for c in cookies if isinstance(c, dict))


# ── the addresses ─────────────────────────────────────────────────────────────────────────────────────────────────
def check_addresses(asked: dict) -> tuple:
    """({name: address} to keep, {name: words} of the ones that are not an address) of what the page sends: "" — the
    site's own."""
    names = {s.name for s in S.SOURCES} | {f"{n}_api" for n in S.API_OF}
    keep, wrong = {}, {}
    for name, value in dict(asked or {}).items():
        name, value = str(name), str(value or "").strip()
        if name not in names or not value:
            continue
        address = S.plain_address(value)
        if not address:
            wrong[name] = "Это не адрес сайта: нужно вроде https://site.org — без пути после него"
        else:
            keep[name] = address
    return keep, wrong
