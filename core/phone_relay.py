"""(v5.44.71) The phone through the owner's own server — «Через сервер, https» (the owner, 5.11.2026: «нельзя придумать
закрытую сеть с шифрованием … по коду или тому же qr без этого ебаного tailscale», «у меня есть vps но на нем vpn
amnezia 3.1 нельзя ли совместить?»).

The program keeps one SSH connection to the server (paramiko — :class:`Keeper`) as the user :data:`USER`, whose key may
do one thing: open the port 127.0.0.1::data:`REMOTE_PORT` on the server for this computer (its line of
``authorized_keys`` — ``core/phone_relay_server.py``).  Beside the server's VPN (Amnezia — not touched) a web server
(Caddy in Docker) answers ``https://<the server's address>[:port]`` with a certificate of Let's Encrypt and passes
every request to that port — through the connection, to this program on 127.0.0.1.  Only the phones paired by a code of
this computer get in (``core/phone_devices.py``, ``phone_access.admit``).  A lost connection (sleep, another network)
is made again with growing pauses; an older connection of this computer the server still holds is closed first (the
key's forced command, ``takeover``: the server would keep its port for hours).  The setup — once, with the server's root
password, which is never kept: ``core/phone_relay_setup.py``.

Settings: ``phone_relay`` — on; ``phone_relay_host``, ``phone_relay_ssh_port`` — the server for SSH;
``phone_relay_address`` — the phone's address (https://…); ``phone_relay_host_key`` — the server's own key as it was at
the setup (another one — refused: someone in between).  This computer's key — ``phone_relay_key`` in the settings'
folder; it never goes to a page or a journal.
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_LOG = logging.getLogger("local_booru.phone")

USER = "lbtunnel"                      # the server's user of this program: port forwarding only
REMOTE_PORT = 18765                    # 127.0.0.1:<this> on the server — the web server passes the phone's requests here
KEY_NAME = "phone_relay_key"           # this computer's key, in the settings' folder
KEY_PATH: "Path | None" = None         # the checks put their own file here
CONNECT_TIMEOUT = 15
WATCH = 30.0                           # seconds between «are you there?» to the server
WATCH_WAIT = 20.0                      # … and how long the answer is waited for
PAUSES = (2, 4, 8, 15, 30, 60)         # seconds before connecting again, growing
LONG_PAUSE = 300                       # after an answer that only the owner can mend (another key of the server)
CHUNK = 64 * 1024
KEYS = ("phone_relay", "phone_relay_host", "phone_relay_ssh_port", "phone_relay_address", "phone_relay_host_key")


class RelayError(Exception):
    """Said to the person as it is."""


class HostKeyChanged(RelayError):
    pass


def available() -> bool:
    """paramiko is there (the program's .venv since 5.44.71)."""
    try:
        import paramiko  # noqa: F401
    except ImportError:
        return False
    return True


# ── what the settings say ───────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Config:
    host: str
    ssh_port: int
    host_key: str                      # «ssh-ed25519 AAAA…» — the server's key at the setup
    address: str                       # https://<the server>[:port] — the phone's address


def config_of(settings: dict) -> "Config | None":
    """The relay the settings name, None — off or not set up."""
    if not settings.get("phone_relay"):
        return None
    host = str(settings.get("phone_relay_host") or "").strip()
    host_key = str(settings.get("phone_relay_host_key") or "").strip()
    address = str(settings.get("phone_relay_address") or "").strip()
    try:
        port = int(settings.get("phone_relay_ssh_port") or 22)
    except (TypeError, ValueError):
        port = 22
    if not (host and host_key and address.startswith("https://")):
        return None
    return Config(host=host, ssh_port=port, host_key=host_key, address=address)


def public_host(settings: dict) -> str:
    """The host of the phone's address (no port, lower case): «203.0.113.5», «203-0-113-5.sslip.io»; "" — off."""
    cfg = config_of(settings)
    if cfg is None:
        return ""
    return (urlparse(cfg.address).hostname or "").lower()


def origin(settings: dict) -> str:
    """The phone's pages' origin: «https://203.0.113.5:8443» (no port for 443); "" — off."""
    cfg = config_of(settings)
    if cfg is None:
        return ""
    parsed = urlparse(cfg.address)
    host = (parsed.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}" + (f":{parsed.port}" if parsed.port and parsed.port != 443 else "")


# ── this computer's key ─────────────────────────────────────────────────────────────────────────────────────────
def key_path() -> Path:
    if KEY_PATH is not None:
        return Path(KEY_PATH)
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / KEY_NAME


def ensure_key(path: "Path | None" = None) -> tuple:
    """(the key for paramiko, its public line «ssh-ed25519 AAAA… local-booru») — made once (Ed25519)."""
    import paramiko
    path = Path(path or key_path())
    if not path.is_file():
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        made = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                                          serialization.NoEncryption())
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(made)
        if os.name != "nt":
            os.chmod(part, 0o600)
        os.replace(part, path)
    key = paramiko.Ed25519Key(filename=str(path))
    return key, f"{key.get_name()} {key.get_base64()} local-booru"


def drop_key(path: "Path | None" = None) -> None:
    try:
        Path(path or key_path()).unlink()
    except FileNotFoundError:
        pass


def key_line(key) -> str:
    """A server's key as the settings keep it: «ssh-ed25519 AAAA…»."""
    return f"{key.get_name()} {key.get_base64()}"


def same_key(seen: str, kept: str) -> bool:
    a, b = str(seen or "").split(), str(kept or "").split()
    return len(a) >= 2 and len(b) >= 2 and a[0] == b[0] and secrets.compare_digest(a[1].encode(), b[1].encode())


# ── «are you there?» of the web server and of the setup ──────────────────────────────────────────────────────────
_PROBES: dict = {}                     # nonce -> when asked (the setup's own requests through the server)
_PROBES_LOCK = threading.Lock()


def probe_nonce() -> str:
    """A word the setup sends through the server to this program (``/m/alive?n=…``): the answer carrying it proves the
    whole way — the internet, the server's certificate, the connection — leads here."""
    nonce = secrets.token_hex(12)
    with _PROBES_LOCK:
        now = time.time()
        for old in [n for n, at in _PROBES.items() if now - at > 600]:
            _PROBES.pop(old, None)
        _PROBES[nonce] = now
    return nonce


def probe_answer(nonce: str) -> bool:
    with _PROBES_LOCK:
        return bool(nonce) and nonce in _PROBES


# ── the connection ──────────────────────────────────────────────────────────────────────────────────────────────
def _words(exc: BaseException, cfg: Config) -> str:
    """Why the connection is not there, for the page."""
    import paramiko
    if isinstance(exc, RelayError):
        return str(exc)
    if isinstance(exc, socket.gaierror):
        return f"Адрес сервера {cfg.host} не найден — нет интернета?"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"Сервер {cfg.host} не отвечает (порт {cfg.ssh_port}) — нет интернета или сервер выключен"
    if isinstance(exc, ConnectionRefusedError):
        return f"Сервер {cfg.host} не принимает соединения на порту {cfg.ssh_port}"
    if isinstance(exc, paramiko.AuthenticationException):
        return "Сервер не пустил программу по её ключу — настройте связь заново («Подключить сервер»)"
    if isinstance(exc, OSError):
        return f"Нет связи с сервером {cfg.host}: {exc.strerror or exc}"
    from core.redaction import sanitize_text
    return "Связь с сервером оборвалась: " + sanitize_text(f"{exc}" or type(exc).__name__)[:200]


class Keeper:
    """The connection to the server, kept while the program runs: made again when lost."""

    def __init__(self, cfg: Config, port: int, key_file: "Path | None" = None, *, pauses=PAUSES, watch=WATCH,
                 remote_port: int = REMOTE_PORT):
        self.cfg = cfg
        self.port = int(port)
        self.key_file = Path(key_file or key_path())
        self.pauses = tuple(pauses)
        self.watch = float(watch)
        self.remote_port = int(remote_port)
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None
        self._transport = None
        self._lock = threading.Lock()
        self.state = {"state": "connecting", "why": "", "since": time.time(), "up_since": 0.0, "tries": 0}
        self.connections = 0                 # requests of phones passed (the checks look at it)

    # the page's view of it
    def public(self) -> dict:
        with self._lock:
            return dict(self.state, address=self.cfg.address)

    def _set(self, state: str, why: str = "") -> None:
        with self._lock:
            if self.state["state"] != state or self.state["why"] != why:
                self.state.update(state=state, why=why, since=time.time())
            if state == "up":
                self.state.update(up_since=time.time(), tries=0)

    def start(self) -> "Keeper":
        self._thread = threading.Thread(target=self._run, name="phone-relay", daemon=True)
        self._thread.start()
        return self

    def stop(self, wait: float = 5.0) -> None:
        self._stop.set()
        transport = self._transport
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:  # noqa: BLE001 - closing a connection that is going away anyway
                _LOG.debug("relay: close: %s", exc)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(wait)
        self._set("off")

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        tries = 0
        while not self._stop.is_set():
            pause = self.pauses[min(tries, len(self.pauses) - 1)]
            try:
                was_up = self._session()
                tries = 0 if was_up else tries + 1
                if not self._stop.is_set():
                    self._set("connecting", "Связь с сервером прервалась — соединяюсь снова")
                    _LOG.info("relay: the connection to %s ended — again", self.cfg.host)
                pause = self.pauses[0] if was_up else pause
            except HostKeyChanged as exc:
                self._set("down", str(exc))
                _LOG.warning("relay: %s", exc)
                pause = LONG_PAUSE
            except Exception as exc:  # noqa: BLE001 - any trouble of the network: said, tried again later
                tries += 1
                words = _words(exc, self.cfg)
                self._set("down", words)
                _LOG.info("relay: %s (%s: %s) — again in %s s", words, type(exc).__name__, exc, pause)
            with self._lock:
                self.state["tries"] = tries
            self._stop.wait(pause)

    def _session(self) -> bool:
        """One connection: made, the port opened on the server, kept until it ends.  True — it was up."""
        import paramiko
        self._set("connecting", self.state["why"])
        key = paramiko.Ed25519Key(filename=str(self.key_file))
        sock = socket.create_connection((self.cfg.host, self.cfg.ssh_port), timeout=CONNECT_TIMEOUT)
        transport = paramiko.Transport(sock)
        self._transport = transport
        try:
            transport.banner_timeout = CONNECT_TIMEOUT
            transport.start_client(timeout=CONNECT_TIMEOUT)
            seen = key_line(transport.get_remote_server_key())
            if not same_key(seen, self.cfg.host_key):
                raise HostKeyChanged("Ключ сервера не тот, что был при настройке: это может быть чужой сервер "
                                     "посередине. Если сервер переустанавливали — настройте связь заново")
            transport.auth_publickey(USER, key)
            if self._stop.is_set():
                return False
            self._takeover(transport)
            self._forward(transport)
            transport.set_keepalive(int(self.watch))
            self._set("up")
            _LOG.info("relay: connected to %s — the phone's address %s", self.cfg.host, self.cfg.address)
            while transport.is_active() and not self._stop.is_set():
                self._stop.wait(self.watch)
                if self._stop.is_set() or not transport.is_active():
                    break
                if not self._answers(transport):
                    _LOG.info("relay: the server did not answer for %s s — the connection is made again", WATCH_WAIT)
                    break
            return True
        finally:
            self._transport = None
            try:
                transport.close()
            except Exception as exc:  # noqa: BLE001 - closing a dead connection
                _LOG.debug("relay: close: %s", exc)

    @staticmethod
    def _answers(transport) -> bool:
        """The server answers a request (OpenSSH says «no» to «keepalive@openssh.com» — an answer all the same)."""
        done = threading.Event()

        def ask():
            try:
                transport.global_request("keepalive@openssh.com", wait=True)
            except Exception as exc:  # noqa: BLE001 - the connection went: the waiter sees it
                _LOG.debug("relay: keepalive: %s", exc)
            finally:
                done.set()
        threading.Thread(target=ask, name="phone-relay-alive", daemon=True).start()
        return done.wait(WATCH_WAIT) and transport.is_active()

    @staticmethod
    def _takeover(transport) -> None:
        """The key's forced command: the server closes this computer's older connections (one that died in sleep still
        holds the port)."""
        try:
            channel = transport.open_session(timeout=CONNECT_TIMEOUT)
            channel.exec_command("takeover")
            channel.status_event.wait(CONNECT_TIMEOUT)
            channel.close()
        except Exception as exc:  # noqa: BLE001 - the port may be free anyway: the forward request says
            _LOG.info("relay: takeover: %s: %s", type(exc).__name__, exc)

    def _forward(self, transport) -> None:
        import paramiko
        last = None
        for _attempt in range(5):
            try:
                transport.request_port_forward("127.0.0.1", self.remote_port, handler=self._incoming)
                return
            except paramiko.SSHException as exc:       # the older connection's port not let go yet
                last = exc
                if self._stop.wait(1.0):
                    return
        raise RelayError(f"Сервер не дал открыть канал для телефона ({last}) — настройте связь заново")

    def _incoming(self, channel, origin_address, server_address) -> None:
        """A request of a phone (the web server of the server opened the port): to this program."""
        self.connections += 1
        threading.Thread(target=self._pipe, args=(channel,), name="phone-relay-pipe", daemon=True).start()

    def _pipe(self, channel) -> None:
        try:
            local = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        except OSError as exc:
            _LOG.info("relay: the program's port %s did not answer: %s", self.port, exc)
            channel.close()
            return
        local.settimeout(None)

        def to_program():
            try:
                while True:
                    data = channel.recv(CHUNK)
                    if not data:
                        break
                    local.sendall(data)
            except (OSError, EOFError) as exc:
                _LOG.debug("relay: to the program: %s", exc)
            finally:
                try:
                    local.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        inward = threading.Thread(target=to_program, name="phone-relay-in", daemon=True)
        inward.start()
        try:
            while True:
                data = local.recv(CHUNK)
                if not data:
                    break
                channel.sendall(data)
        except (OSError, EOFError) as exc:
            _LOG.debug("relay: to the phone: %s", exc)
        finally:
            try:
                channel.shutdown_write()
            except (OSError, EOFError):
                pass
            inward.join(60)
            try:
                channel.close()
            except (OSError, EOFError) as exc:          # the connection to the server went meanwhile
                _LOG.debug("relay: channel close: %s", exc)
            local.close()


# ── the one kept by the program ────────────────────────────────────────────────────────────────────────────────
_KEEPER: dict = {"keeper": None}
_KEEPER_LOCK = threading.Lock()
SETUP_GOING = threading.Event()          # the setup runs its own connection: the duty leaves it alone


def start(cfg: Config, port: int, key_file: "Path | None" = None) -> Keeper:
    """The connection for these settings and this run's port (another one before — closed)."""
    with _KEEPER_LOCK:
        old = _KEEPER["keeper"]
        if old is not None and old.alive() and old.cfg == cfg and old.port == int(port):
            return old
        if old is not None:
            old.stop(wait=2.0)
        keeper = Keeper(cfg, port, key_file).start()
        _KEEPER["keeper"] = keeper
        return keeper


def stop() -> None:
    with _KEEPER_LOCK:
        old = _KEEPER["keeper"]
        _KEEPER["keeper"] = None
    if old is not None:
        old.stop(wait=2.0)


def current() -> "Keeper | None":
    return _KEEPER["keeper"]


def keep(settings: dict, port: int) -> None:
    """The duty ``phone``: on — the connection is there (for this run's port); off — none."""
    if SETUP_GOING.is_set():
        return
    cfg = config_of(settings)
    if cfg is None or not port or not available() or not key_path().is_file():
        if current() is not None:
            stop()
        return
    start(cfg, port)


def state(settings: dict) -> dict:
    """The connection for the page: {"state": "off|connecting|up|down", "why", "since", "address"}."""
    cfg = config_of(settings)
    keeper = current()
    if cfg is None:
        return {"state": "off", "why": "", "since": 0, "address": ""}
    if keeper is None or keeper.cfg != cfg:
        why = "" if available() else "Нет модуля paramiko — переустановите программу"
        return {"state": "connecting" if available() else "down", "why": why, "since": 0, "address": cfg.address}
    return keeper.public()
