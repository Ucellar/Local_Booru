"""(v5.44.64) The library on the owner's phone away from home — through Tailscale.

The owner (4.11.2026): «можно ли сделать связь с телефоном не по локалке а именно пока комп врублен я могу зайти в
библиотеку и с телефона смотреть или читать»; of the ways offered — «с прогой дак с прогой»: Tailscale, installed by
him on the computer and on the phone under one account, joins his devices into a network of their own (a tunnel of
Cloudflare is cut in Russia since 9.06.2025; a port open on the router shows the library to the whole internet).

The program's server stays on 127.0.0.1.  ``tailscale serve`` takes ``https://<computer>.<tailnet>.ts.net`` — with
Tailscale's own certificate — to it, and writes on every request who sent it: ``Tailscale-User-Login``.  A request is
the phone's (:func:`admit`) when it came that way — the computer's name in the tailnet as its host (``serve`` keeps
the phone's Host; were the host this computer's own address, the name must be in ``X-Forwarded-Host``), from this
computer's own Tailscale (127.0.0.1) — and its login is the account this computer's Tailscale is in: no password and
no pairing, only the owner's devices are in his tailnet.  A POST of the phone comes from that address
(:func:`origin_ok`); a page of another site may open the library only as a page, not take its pictures or answers
into itself (``Sec-Fetch-Site``) — what the cookie ``SameSite=Strict`` does for the computer.

Settings: ``phone_access`` — «Пускать телефон»; ``phone_name``, ``phone_login`` — what Tailscale said when it was
switched on; ``webui_port`` — a port of its own (the address Tailscale takes the phone to must not change between the
runs of the program).  The duty ``phone`` (``core/background_duties``) keeps Tailscale pointed at this run's port.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass

_LOG = logging.getLogger("local_booru.phone")

CLI: "str | list" = ""                    # the tailscale command; "" — looked for (the checks put a stand-in here)
CLI_PLACES = (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe",
              "/usr/bin/tailscale", "/usr/local/bin/tailscale",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
STATUS_TIMEOUT = 12                       # seconds for «tailscale status»
SERVE_TIMEOUT = 25                        # «tailscale serve» waits for the tailnet's permission — it gives a link then
ENABLE_LINK = re.compile(r"https://login\.tailscale\.com/\S+")
KEYS = ("phone_access", "phone_name", "phone_login", "webui_port")
LOOPBACK = ("127.0.0.1", "localhost", "[::1]")


# ── the tailscale command ────────────────────────────────────────────────────────────────────────────────────────
def _command() -> list:
    if CLI:
        return list(CLI) if isinstance(CLI, (list, tuple)) else [str(CLI)]
    found = shutil.which("tailscale") or next((p for p in CLI_PLACES if os.path.isfile(p)), "")
    return [found] if found else []


def cli() -> str:
    """The tailscale command of this computer, "" — none."""
    command = _command()
    return command[0] if command else ""


def _run(args: list, timeout: float) -> tuple:
    """(exit code or None when it did not finish, what it printed).  No window of its own on Windows."""
    command = _command()
    if not command:
        return None, ""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        done = subprocess.run([*command, *args], capture_output=True, timeout=timeout, creationflags=flags,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + (exc.stderr or b"")
        return None, out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
    except OSError as exc:
        _LOG.warning("tailscale %s: %s", args[:1], exc)
        return None, ""
    return done.returncode, (done.stdout or b"").decode("utf-8", "replace") + (done.stderr or b"").decode(
        "utf-8", "replace")


@dataclass
class State:
    installed: bool = False
    running: bool = False                 # in its account and connected
    name: str = ""                        # the computer's name in the tailnet: desktop-x.tail1234.ts.net
    login: str = ""                       # the account
    https: bool = False                   # the tailnet gives this computer a certificate
    words: str = ""                       # what is wrong, for the page

    def public(self) -> dict:
        return {"installed": self.installed, "running": self.running, "name": self.name,
                "account": masked(self.login), "https": self.https, "words": self.words}


def masked(login: str) -> str:
    """The account as the page shows it: «a…@gmail.com» — enough to know it, not the whole of it."""
    login = str(login or "")
    if "@" not in login:
        return login[:1] + "…" if login else ""
    user, host = login.split("@", 1)
    return f"{user[:1]}…@{host}"


def status() -> State:
    """What this computer's Tailscale is: installed, in an account and connected, its name, the account."""
    if not cli():
        return State(words="Tailscale на этом компьютере не установлен")
    code, out = _run(["status", "--json"], STATUS_TIMEOUT)
    state = State(installed=True)
    try:
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not data:
        state.words = "Tailscale не ответил — запущен ли он?" if code is None or code else "Tailscale не ответил"
        return state
    backend = str(data.get("BackendState") or "")
    me = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    state.name = str(me.get("DNSName") or "").rstrip(".").lower()
    users = data.get("User") if isinstance(data.get("User"), dict) else {}
    user = users.get(str(me.get("UserID"))) if me.get("UserID") is not None else None
    state.login = str((user or {}).get("LoginName") or "") if isinstance(user, dict) else ""
    domains = [str(d).rstrip(".").lower() for d in data.get("CertDomains") or []]
    state.https = bool(state.name) and state.name in domains
    if backend == "NeedsLogin":
        state.words = "Tailscale не вошёл в аккаунт — откройте его на компьютере и войдите"
    elif backend in ("Stopped", "NoState"):
        state.words = "Tailscale выключен — включите его на компьютере"
    elif backend != "Running":
        state.words = f"Tailscale пока не готов ({backend or 'нет ответа'})"
    elif not state.name:
        state.words = "Tailscale не назвал имя этого компьютера в сети"
    else:
        state.running = True
    return state


def serve_target() -> str:
    """Where Tailscale sends https://<name> now: «http://127.0.0.1:PORT» or ""."""
    code, out = _run(["serve", "status", "--json"], STATUS_TIMEOUT)
    try:
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
    except ValueError:
        return ""
    for _where, web in (data.get("Web") or {}).items() if isinstance(data, dict) else []:
        handlers = web.get("Handlers") if isinstance(web, dict) else None
        root = (handlers or {}).get("/") if isinstance(handlers, dict) else None
        if isinstance(root, dict) and root.get("Proxy"):
            return str(root["Proxy"])
    return ""


def serve(port: int) -> tuple:
    """Tailscale sends https://<name> to this program's port.  (done, words, a link the owner opens once to let his
    tailnet serve — "" when not asked)."""
    target = f"http://127.0.0.1:{int(port)}"
    code, out = _run(["serve", "--bg", "--https=443", target], SERVE_TIMEOUT)
    link = ENABLE_LINK.search(out or "")
    if code == 0 and serve_target().rstrip("/") == target:
        return True, "", ""
    if link:
        return False, ("Tailscale просит один раз разрешить для вашей сети HTTPS и раздачу (Serve): откройте ссылку, "
                       "нажмите «Enable» и затем «Проверить снова»"), link.group(0).rstrip(".,)")
    tail = " ".join((out or "").split())[-300:]
    from core.redaction import sanitize_text
    return False, "Tailscale не взялся пускать к программе" + (f": {sanitize_text(tail)}" if tail else ""), ""


def unserve() -> None:
    _run(["serve", "--https=443", "off"], SERVE_TIMEOUT)


# ── who a request is ────────────────────────────────────────────────────────────────────────────────────────────
def _header(headers, name: str) -> str:
    """A header of the request whatever the case of its name (``email.message.Message`` and a plain dict alike)."""
    value = headers.get(name) if hasattr(headers, "get") else None
    if value is None and isinstance(headers, dict):
        low = name.lower()
        value = next((v for k, v in headers.items() if str(k).lower() == low), None)
    return str(value or "")


def _host_only(value) -> str:
    """The host of a Host header without its port: «name:443» → «name», «[::1]:80» → «[::1]»."""
    host = str(value or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[:end + 1] if end > 0 else host
    return (host.rsplit(":", 1)[0] if host.count(":") == 1 else host).rstrip(".")


def _login(value: str) -> str:
    """``Tailscale-User-Login`` as Tailscale writes it: plain, or RFC 2047 («=?utf-8?q?…?=») when not ASCII."""
    value = str(value or "").strip()
    if value.startswith("=?"):
        try:
            from email.header import decode_header, make_header
            value = str(make_header(decode_header(value)))
        except Exception:  # noqa: BLE001 - a header that does not decode: no login
            return ""
    return value.strip().lower()


def _cross_site(headers) -> bool:
    """Another site takes the library's pictures or answers into itself (a page of it may only open the library)."""
    return (_header(headers, "Sec-Fetch-Site").strip().lower() == "cross-site"
            and _header(headers, "Sec-Fetch-Mode").strip().lower() != "navigate")


def _relay(settings: dict, headers, peer: str) -> "str | None":
    """(v5.44.71) The request came through the owner's server (``core/phone_relay``): its address is the Host, the
    connection is the program's own (the server's way ends in this program on 127.0.0.1).  «ok» — a phone paired with
    this computer (its cookie, ``core/phone_devices``); «pair» — not paired yet: only the pairing page and the files of
    the app; «other» — not that way; None — not the server's address."""
    from core import phone_relay
    relay = phone_relay.public_host(settings)
    if not relay or _host_only(_header(headers, "Host")).strip("[]") != relay:
        return None
    if str(peer or "") not in ("127.0.0.1", "::1"):
        return "other"
    if _cross_site(headers):
        return "other"
    from core import phone_devices
    return "ok" if phone_devices.check(phone_devices.cookie_of(headers)) else "pair"


OPEN_TO_PAIR = ("/m/pair", "/m/manifest.webmanifest", "/m/sw.js", "/m/offline", "/m/alive")


def open_to_pair(path: str) -> bool:
    """(v5.44.71) What a phone not paired yet may have through the server: the pairing page, the app's description, its
    icons and its worker (a browser asks for them without the cookie), «Компьютер не отвечает»."""
    return path in OPEN_TO_PAIR or path.startswith("/m/icon/") or path.startswith("/static/")


def admit(settings: dict, headers, peer: str) -> "str | None":
    """«ok» — the owner's phone through Tailscale (or, v5.44.71, through his own server with a paired phone's cookie);
    «pair» — through his server, a phone not paired yet; «other» — it came to the computer's name in the tailnet (or
    the server's address) but not as the owner (another account, or not through this computer's Tailscale); None — not
    a request of the phone."""
    relay = _relay(settings, headers, peer)
    if relay is not None:
        return relay
    if not settings.get("phone_access"):
        return None
    name = str(settings.get("phone_name") or "").rstrip(".").lower()
    if not name:
        return None
    host = _host_only(_header(headers, "Host"))
    # X-Forwarded-Host only beside this computer's own address: a page of another site that made its name lead to
    # 127.0.0.1 (a DNS trick) has that name as the Host — and may write any X-Forwarded-Host it likes.
    if host != name and not (host in LOOPBACK and _host_only(_header(headers, "X-Forwarded-Host")) == name):
        return None
    if str(peer or "") not in ("127.0.0.1", "::1"):
        return "other"                                    # Tailscale serve connects from this very computer
    if _cross_site(headers):
        return "other"                                    # another site taking the library's pictures or answers
    login = _login(_header(headers, "Tailscale-User-Login"))
    owner = str(settings.get("phone_login") or "").strip().lower()
    if login and owner and secrets.compare_digest(login.encode("utf-8"), owner.encode("utf-8")):
        return "ok"
    return "other"


def origin_ok(origin: str, settings: dict, port: int = 0) -> bool:
    """A POST of the phone comes from its address — ``https://<name>``; ``http://<name>:<port>`` — this server's own
    port under the name (nothing but this computer can lead the name there: the checks' browser does); (v5.44.71)
    through the owner's server — its https address exactly."""
    origin = str(origin or "").strip().lower()
    from core import phone_relay
    relay = phone_relay.origin(settings)
    if relay and (origin == relay or (port and origin == f"http://{phone_relay.public_host(settings)}:{int(port)}")):
        return True                                       # (the second — the checks' browser, as for Tailscale)
    name = str(settings.get("phone_name") or "").rstrip(".").lower()
    allowed = {f"https://{name}", f"http://{name}"} | ({f"http://{name}:{int(port)}"} if port else set())
    return bool(settings.get("phone_access") and name and origin in allowed)


def is_phone(req) -> bool:
    """The page is asked by the owner's phone (``webui`` pages ask this of their request)."""
    return admit(req.settings, req.headers, getattr(req, "peer", "")) == "ok"


def address(settings: dict) -> str:
    name = str(settings.get("phone_name") or "").rstrip(".").lower()
    return f"https://{name}/" if name else ""


# ── switching it on and off ─────────────────────────────────────────────────────────────────────────────────────
def switch_on(port: int) -> dict:
    """«Пускать телефон»: Tailscale must be in its account and connected; it is told to send its https address to this
    program's port.  {ok, words, link, changes — what the settings keep}."""
    state = status()
    if not state.running:
        return {"ok": False, "words": state.words, "link": "", "changes": {}}
    if not state.login:
        return {"ok": False, "words": "Tailscale не назвал аккаунт этого компьютера", "link": "", "changes": {}}
    done, words, link = serve(port)
    changes = {"phone_access": True, "phone_name": state.name, "phone_login": state.login, "webui_port": int(port)}
    return {"ok": done, "words": words, "link": link, "changes": changes if done else {}}


def switch_off() -> dict:
    unserve()
    return {"ok": True, "changes": {"phone_access": False}}


def keep(settings: dict, port: int) -> dict:
    """The duty ``phone``: with the phone let in, Tailscale still sends its address to this run's port (another port
    after a busy one, the computer renamed in the tailnet).  The settings to change — {} when nothing."""
    if not settings.get("phone_access") or not port or not cli():
        return {}
    state = status()
    if not state.running:
        return {}
    changes = {}
    if state.name and state.name != str(settings.get("phone_name") or "").lower():
        changes["phone_name"] = state.name
    if state.login and state.login != str(settings.get("phone_login") or ""):
        changes["phone_login"] = state.login
    if serve_target().rstrip("/") != f"http://127.0.0.1:{int(port)}":
        done, words, _link = serve(port)
        if not done:
            _LOG.info("phone: Tailscale not pointed at port %s: %s", port, words)
    return changes
