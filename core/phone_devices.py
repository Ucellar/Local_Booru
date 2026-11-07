"""(v5.44.71) The phones let in through the owner's own server (``core/phone_relay.py``).

A phone is paired once, by a code the computer shows on «Настройки → Телефон»: a QR code with the address
``/m/pair?c=<code>`` or eight digits typed on the page ``/m/pair``.  A code lives :data:`CODE_LIFE` and serves one
phone; :data:`TRIES` wrong guesses of the digits burn it.  The phone then has its own long cookie (:data:`COOKIE`,
``HttpOnly; Secure; SameSite=Lax``, a year); the program keeps only its SHA-256 (``phone_devices.json`` in the
settings' folder) with a name made of the phone's browser and system, when it was paired and last seen.  Each one is
switched off on the computer by itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path

_LOG = logging.getLogger("local_booru.phone")

COOKIE = "lb_phone"
COOKIE_LIFE = 365 * 24 * 3600
CODE_LIFE = 15 * 60                     # seconds a code of the computer serves
TRIES = 8                               # wrong eight digits — the code is burned
SEEN_EVERY = 10 * 60                    # «last seen» is written at most this often
FILE_NAME = "phone_devices.json"
PATH: "Path | None" = None              # the checks put their own file here

_LOCK = threading.Lock()
_CODE: dict = {}                        # {"long", "short", "until", "tries"} — one code at a time, in memory only
_KEPT: dict = {"mtime": None, "devices": []}
_WRITTEN = {"at": 0.0}


def _path() -> Path:
    if PATH is not None:
        return Path(PATH)
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / FILE_NAME


def _hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


# ── the code of the computer ────────────────────────────────────────────────────────────────────────────────────
def new_code() -> dict:
    """A new code (the one before stops working): {"long" — for the QR code's address, "short" — eight digits,
    "until" — epoch seconds}."""
    with _LOCK:
        _CODE.clear()
        _CODE.update(long=secrets.token_urlsafe(16), short=f"{secrets.randbelow(10 ** 8):08d}",
                     until=time.time() + CODE_LIFE, tries=0)
        return {k: _CODE[k] for k in ("long", "short", "until")}


def code() -> "dict | None":
    """The code that still works ({"long", "short", "until"}), None — none."""
    with _LOCK:
        if not _CODE or _CODE["until"] <= time.time():
            _CODE.clear()
            return None
        return {k: _CODE[k] for k in ("long", "short", "until")}


def take(*, long: str = "", short: str = "") -> bool:
    """The phone brings a code: True — it is the computer's (the code is used up); a wrong one counts (eight digits
    :data:`TRIES` times wrong — the code is burned)."""
    digits = re.sub(r"\D", "", str(short or ""))
    with _LOCK:
        if not _CODE or _CODE["until"] <= time.time():
            _CODE.clear()
            return False
        right = bool(long) and secrets.compare_digest(str(long).encode("utf-8"), _CODE["long"].encode("utf-8"))
        right = right or (len(digits) == 8 and secrets.compare_digest(digits.encode("ascii"),
                                                                       _CODE["short"].encode("ascii")))
        if right:
            _CODE.clear()
            return True
        _CODE["tries"] += 1
        if _CODE["tries"] >= TRIES:
            _LOG.warning("phone: the pairing code was guessed wrong %d times — burned", TRIES)
            _CODE.clear()
        return False


# ── the phones ──────────────────────────────────────────────────────────────────────────────────────────────────
def _devices() -> list:
    """The phones (the file read again when it changed)."""
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if mtime != _KEPT["mtime"]:
        devices = []
        if mtime:
            from core.file_safety import read_json_kept
            try:
                data = read_json_kept(path, dict, what="phone devices") or {}
                devices = [d for d in data.get("devices") or [] if isinstance(d, dict) and d.get("hash")]
            except OSError as exc:
                _LOG.warning("phone devices not read: %s", exc)
                return list(_KEPT["devices"])
        _KEPT.update(mtime=mtime, devices=devices)
    return _KEPT["devices"]


def _save(devices: list) -> None:
    from core.file_safety import write_json_kept
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_kept(path, json.dumps({"devices": devices}, ensure_ascii=False, indent=1), dict)
    try:
        _KEPT.update(mtime=path.stat().st_mtime, devices=devices)
    except OSError:
        _KEPT.update(mtime=None, devices=devices)
    _WRITTEN["at"] = time.time()


def device_name(user_agent: str) -> str:
    """«Android 14 · Chrome 131», «iPhone · Safari» — from the browser's User-Agent."""
    ua = str(user_agent or "")
    system = ""
    found = re.search(r"Android (\d+(?:\.\d+)?)", ua)
    if found:
        system = f"Android {found.group(1)}"
    elif "iPhone" in ua:
        system = "iPhone"
    elif "iPad" in ua:
        system = "iPad"
    elif "Windows" in ua:
        system = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "Mac"
    elif "Linux" in ua:
        system = "Linux"
    browser = ""
    for name, pattern in (("Яндекс Браузер", r"YaBrowser/(\d+)"), ("Samsung Internet", r"SamsungBrowser/(\d+)"),
                          ("Edge", r"Edg[AiA]?/(\d+)"), ("Opera", r"OPR/(\d+)"), ("Firefox", r"(?:Firefox|FxiOS)/(\d+)"),
                          ("Chrome", r"(?:Chrome|CriOS)/(\d+)"), ("Safari", r"Version/(\d+)[^ ]* (?:Mobile/\S+ )?Safari")):
        found = re.search(pattern, ua)
        if found:
            browser = f"{name} {found.group(1)}"
            break
    return " · ".join(x for x in (system, browser) if x) or "Телефон"


def pair(user_agent: str = "") -> str:
    """A new phone: its cookie's value (kept here only as a hash)."""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with _LOCK:
        devices = list(_devices())
        device = {"id": secrets.token_hex(6), "name": device_name(user_agent), "hash": _hash(token), "at": now,
                  "seen": now}
        devices.append(device)
        _save(devices)
    _LOG.info("phone: a phone paired — %s", device["name"])
    return token


def check(token: str) -> str:
    """The phone's id by its cookie; "" — not a phone of this computer.  Its «last seen» is kept."""
    if not token:
        return ""
    wanted = _hash(token)
    with _LOCK:
        devices = _devices()
        device = next((d for d in devices if secrets.compare_digest(str(d.get("hash")), wanted)), None)
        if device is None:
            return ""
        now = int(time.time())
        if now - int(device.get("seen") or 0) > 60:
            device["seen"] = now
            if time.time() - _WRITTEN["at"] > SEEN_EVERY:
                try:
                    _save(list(devices))
                except OSError as exc:
                    _LOG.info("phone devices: «last seen» not written: %s", exc)
        return str(device.get("id") or "")


def remove(device_id: str = "") -> int:
    """A phone switched off («Отключить»); "" — all of them.  -> how many."""
    with _LOCK:
        devices = list(_devices())
        kept = [d for d in devices if device_id and d.get("id") != device_id]
        if len(kept) == len(devices):
            return 0
        _save(kept)
    return len(devices) - len(kept)


def listing() -> list:
    """The phones for the page: {"id", "name", "at", "seen"} — the newest first."""
    with _LOCK:
        devices = [{k: d.get(k) for k in ("id", "name", "at", "seen")} for d in _devices()]
    return sorted(devices, key=lambda d: -int(d.get("at") or 0))


def cookie_of(headers) -> str:
    """The value of :data:`COOKIE` in a request's headers ("" — none)."""
    from http.cookies import CookieError, SimpleCookie
    raw = headers.get("Cookie", "") if hasattr(headers, "get") else ""
    if not raw and isinstance(headers, dict):                # a plain dict of a request: any case of the name
        raw = next((v for k, v in headers.items() if str(k).lower() == "cookie"), "")
    try:
        morsel = SimpleCookie(str(raw or "")).get(COOKIE)
    except CookieError:
        return ""
    return morsel.value if morsel is not None else ""


def set_cookie(token: str, secure: bool = True) -> str:
    """The phone's cookie; ``secure`` — the request came through https (the server's web server says so: without it,
    the checks' browser on plain http, a browser would not keep a «Secure» cookie)."""
    return f"{COOKIE}={token}; Path=/; Max-Age={COOKIE_LIFE}; HttpOnly{'; Secure' if secure else ''}; SameSite=Lax"
