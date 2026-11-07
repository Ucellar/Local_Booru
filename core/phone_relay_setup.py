"""(v5.44.71) Setting the owner's server up for the phone, once (``core/phone_relay.py``).

The owner gives the server's address and its root login and password — the ones Amnezia asked for (another user with
sudo works too).  The password is used for this setup only and kept nowhere — not in the settings, not in a journal, not
on a page (a line of the server that would show it is masked): with it the program looks at the server
(``phone_relay_server.probe_script``), chooses the ports (Amnezia's protocols are left where they are), puts its own
user with this computer's key, the forced command and the web server there (``apply_script``), connects as that user
(the keeper), and waits for the certificate of Let's Encrypt, checking the whole way of the phone — from this computer
through the internet to the server's https address and back here (``/m/alive?n=…``).  An IP address the certificate
was refused for gets a name of sslip.io instead.  :func:`remove` takes it all away again (the password once more).
"""
from __future__ import annotations

import logging
import socket
import time

from core import phone_relay as R
from core import phone_relay_server as S

_LOG = logging.getLogger("local_booru.phone")

SCRIPT_TIMEOUT = 420           # seconds for a script (Docker downloads Caddy: ~50 MB)
CERT_WAIT = 240                # seconds the certificate is waited for
PROBE_EVERY = 5
LOGS_EVERY = 15
STEPS = {"user": f"Завожу на сервере отдельный вход программы ({R.USER}): только канал для телефона, без командной строки",
         "takeover": "", "caddy": "Пишу настройку веб-сервера (/opt/local-booru)",
         "pull": "Скачиваю веб-сервер Caddy (Docker, около 50 МБ)…", "run": "Запускаю веб-сервер рядом с VPN",
         "firewall": ""}
FAILS = {"useradd": "не завёлся пользователь", "usermod": "не завёлся пользователь", "home": "нет его домашней папки",
         "ssh-dir": "не записалась папка ключей", "keys": "не записался ключ программы",
         "takeover": "не записалась команда входа", "folder": "не записалась папка /opt/local-booru",
         "pull": "Docker не скачал Caddy — сервер не достаёт до Docker Hub?", "run": "Docker не запустил Caddy",
         "started": "Caddy не запустился", "restart": "Caddy не перезапустился"}


class SetupError(Exception):
    """Said to the person as it is."""


class Root:
    """The server as its root (or a user with sudo): scripts run through one connection."""

    def __init__(self, host: str, ssh_port: int, login: str, password: str, *, say=None):
        import paramiko
        self.host, self.ssh_port, self.login, self._password = host, int(ssh_port), login, password
        self.say = say or (lambda _text: None)
        try:
            sock = socket.create_connection((host, self.ssh_port), timeout=R.CONNECT_TIMEOUT)
        except socket.gaierror:
            raise SetupError(f"Адрес {host} не найден") from None
        except (TimeoutError, socket.timeout):
            raise SetupError(f"Сервер {host} не отвечает на порту {self.ssh_port} — адрес и порт верные?") from None
        except OSError as exc:
            raise SetupError(f"Сервер {host} не принимает соединения на порту {self.ssh_port}: "
                             f"{exc.strerror or exc}") from None
        self.transport = paramiko.Transport(sock)
        try:
            self.transport.banner_timeout = R.CONNECT_TIMEOUT
            self.transport.start_client(timeout=R.CONNECT_TIMEOUT)
        except (paramiko.SSHException, EOFError, OSError) as exc:
            self.close()
            raise SetupError(f"На порту {self.ssh_port} сервера не SSH или он оборвал соединение ({exc})") from None
        self.banner = str(self.transport.remote_version or "")
        self.host_key = R.key_line(self.transport.get_remote_server_key())
        try:
            self.transport.auth_password(login, password)
        except paramiko.BadAuthenticationType:
            self.close()
            raise SetupError(f"Сервер не пускает «{login}» по паролю (только по ключу). Как в Amnezia: если вы входили "
                             "ключом — эта настройка пока умеет только пароль") from None
        except paramiko.AuthenticationException:
            self.close()
            raise SetupError(f"Сервер не принял логин «{login}» и пароль") from None
        except (paramiko.SSHException, EOFError, OSError) as exc:
            self.close()
            raise SetupError(f"Вход на сервер оборвался: {exc}") from None
        self.root = login == "root"
        self.nopass = False
        if not self.root:
            code, _out = self.run("true", as_root=False, sudo_check=True)
            self.nopass = code == 0

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception as exc:  # noqa: BLE001 - closing what goes away anyway
            _LOG.debug("relay setup: close: %s", exc)

    def _masked(self, text: str) -> str:
        return text.replace(self._password, "***") if self._password else text

    def run(self, script: str, *, as_root: bool = True, sudo_check: bool = False, timeout: float = SCRIPT_TIMEOUT,
            on_line=None, stop=None) -> tuple:
        """(exit code, what it printed — stdout and then stderr) of a bash script on the server."""
        channel = self.transport.open_session(timeout=R.CONNECT_TIMEOUT)
        if sudo_check:
            command = "sudo -n true"
        elif not as_root or self.root:
            command = "bash -s"
        elif self.nopass:
            command = "sudo -n bash -s"
        else:
            command = "sudo -S -k -p '' bash -s"          # -k: sudo always reads the password line (never bash)
        channel.exec_command(command)
        if as_root and not self.root and not self.nopass and not sudo_check:
            channel.sendall((self._password + "\n").encode("utf-8"))
        if not sudo_check:
            channel.sendall(script.encode("utf-8"))
        channel.shutdown_write()
        out, err, pending = [], [], b""
        deadline = time.monotonic() + timeout
        while True:
            if channel.recv_ready():
                data = channel.recv(65536)
                pending += data
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    text = self._masked(line.decode("utf-8", "replace").rstrip("\r"))
                    out.append(text)
                    if on_line is not None:
                        on_line(text)
                continue
            if channel.recv_stderr_ready():
                err.append(self._masked(channel.recv_stderr(65536).decode("utf-8", "replace")))
                continue
            if channel.exit_status_ready():
                break
            if time.monotonic() > deadline or (stop is not None and stop()):
                channel.close()
                raise SetupError("Сервер не ответил вовремя" if time.monotonic() > deadline else "Остановлено")
            time.sleep(0.05)
        if pending:
            out.append(self._masked(pending.decode("utf-8", "replace")))
        code = channel.recv_exit_status()
        channel.close()
        return code, "\n".join(out) + ("\n" + "".join(err) if err else "")


def _step_words(say):
    """The script's «LB-STEP» lines as words of the journal."""
    def on_line(line: str) -> None:
        kind, _, rest = line.partition(" ")
        if kind == "LB-STEP" and STEPS.get(rest.strip()):
            say(STEPS[rest.strip()])
    return on_line


def _failure(out: str) -> str:
    """Why an applying script stopped, in words (its «LB-FAIL» and «LB-LOG» lines)."""
    from core.redaction import sanitize_text
    code, why, logs = "", "", []
    for line in out.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "LB-FAIL":
            code, _, why = rest.strip().partition(" ")
        elif kind == "LB-LOG":
            logs.append(rest.strip())
    words = FAILS.get(code, code or "скрипт остановился")
    tail = sanitize_text(" / ".join(logs[-3:]))[:400]
    return f"На сервере: {words}" + (f" ({why})" if why else "") + (f". Caddy: {tail}" if tail else "")


def probe(address: str) -> str:
    """The phone's way from this computer: «ok» — the program answered through the server with a good certificate;
    «cert» — the certificate is not there (yet); «connect» — the server's port does not answer; «other» — something
    else answered."""
    import requests
    nonce = R.probe_nonce()
    for trust_env in (False, True):                 # straight, as a phone; then through the system's proxy (a VPN)
        session = requests.Session()
        session.trust_env = trust_env
        try:
            answer = session.get(address.rstrip("/") + "/m/alive", params={"n": nonce}, timeout=12,
                                 allow_redirects=False)
        except requests.exceptions.SSLError:
            return "cert"
        except requests.exceptions.RequestException:
            continue
        finally:
            session.close()
        return "ok" if answer.status_code == 204 and answer.headers.get("X-LB-Relay") == "1" else "other"
    return "connect"


def setup(host_text: str, ssh_port, login: str, password: str, *, port: int, settings: dict, say, stop,
          probe_way=probe, cert_wait: float = CERT_WAIT) -> dict:
    """The setup as a page task: {"changes" — the settings, "address", "warn" — what is left to the owner} or
    {"failed": words}."""
    try:
        host = S.clean_host(host_text)
    except S.ServerError as exc:
        return {"failed": str(exc)}
    try:
        ssh_port = int(ssh_port or 22)
    except (TypeError, ValueError):
        return {"failed": "Порт SSH — это число (обычно 22)"}
    login = str(login or "root").strip() or "root"
    if not password:
        return {"failed": "Введите пароль сервера"}
    if not R.available():
        return {"failed": "В программе нет модуля paramiko — он ставится с обновлением программы"}
    say(f"Подключаюсь к серверу {host}:{ssh_port}…")
    try:
        root = Root(host, ssh_port, login, password, say=say)
    except SetupError as exc:
        return {"failed": str(exc)}
    try:
        return _setup(root, host, ssh_port, port=port, settings=settings, say=say, stop=stop, probe_way=probe_way,
                      cert_wait=cert_wait)
    except SetupError as exc:
        return {"failed": str(exc)}
    finally:
        root.close()


def _setup(root: Root, host: str, ssh_port: int, *, port: int, settings: dict, say, stop, probe_way,
           cert_wait: float) -> dict:
    pinned = str(settings.get("phone_relay_host_key") or "")
    if pinned and str(settings.get("phone_relay_host") or "") == host and not R.same_key(root.host_key, pinned):
        return {"failed": "Ключ сервера не тот, что был при прошлой настройке — это может быть чужой сервер "
                          "посередине. Если сервер переустанавливали — сначала «Отключить сервер», потом настройте "
                          "заново"}
    version = S.openssh_version(root.banner)
    say(f"Сервер ответил ({root.banner.removeprefix('SSH-2.0-')[:60]}), вход «{root.login}» принят")
    code, out = root.run(S.probe_script(), stop=stop)
    seen = S.read_probe(out)
    if not seen["complete"]:
        raise SetupError("Сервер не ответил на проверку" + (" — у пользователя нет прав sudo?" if not root.root else ""))
    if not seen["root"]:
        raise SetupError("Команды идут не от root — нужен вход root или пользователь с sudo")
    if seen["docker"] != "ok":
        raise SetupError("На сервере нет Docker — Amnezia ставит его сама; VPN на этом сервере от Amnezia?"
                         if seen["docker"] == "none" else "Docker на сервере не отвечает — он запущен?")
    if seen["missing"]:
        raise SetupError("На сервере нет команд: " + ", ".join(seen["missing"]))
    if seen["ours"]:
        say("На сервере уже есть веб-сервер программы с прошлой настройки — ставлю его заново")
        root.run(f"docker rm -f {S.CONTAINER} >/dev/null 2>&1; sleep 1", stop=stop)
        seen = S.read_probe(root.run(S.probe_script(), stop=stop)[1])
    if not seen["ports_known"]:
        raise SetupError("Не получилось узнать, какие порты на сервере заняты (нет ss и netstat)")
    refusal = S.sshd_refusal(seen)
    if refusal:
        raise SetupError(refusal)
    try:
        https_port, http_free = S.choose_ports(seen)
    except S.ServerError as exc:
        raise SetupError(str(exc)) from None
    taken = seen["taken"]
    if 443 in taken:
        say(f"Порт 443 занят ({taken[443]} — это VPN), телефон пойдёт на {https_port}")
    elif https_port != 443:
        say("Порт 443 остаётся свободным для VPN")
    say(f"Сервер: {seen['os'] or 'Linux'}; порт для телефона — {https_port}"
        + ("; сертификат Let's Encrypt проверит через порт 80" if http_free else "; сертификат — через порт 443"))
    site = host
    if not S.is_ip(host):
        say(f"У сервера имя {host}: сертификат будет на это имя")
    _key, public_line = R.ensure_key()
    listen = not version or version >= (7, 8)
    script = S.apply_script(public_line=public_line, listen=listen, site=site, https_port=https_port,
                            http_free=http_free, ufw=seen["ufw"], firewalld=seen["firewalld"])
    code, out = root.run(script, on_line=_step_words(say), stop=stop)
    if code != 0 or "LB-DONE" not in out:
        raise SetupError(_failure(out))
    if seen["ufw"] or seen["firewalld"]:
        say("Брандмауэр сервера: порт " + str(https_port) + (" и 80 открыты" if http_free else " открыт"))
    address = S.address_of(site, https_port)
    cfg = R.Config(host=host, ssh_port=ssh_port, host_key=root.host_key, address=address)
    keeper = R.start(cfg, port)
    say("Соединяю компьютер с сервером…")
    deadline = time.monotonic() + 40
    while keeper.public()["state"] != "up":
        if time.monotonic() > deadline or stop():
            raise SetupError("Программа не смогла войти на сервер своим ключом: " + (keeper.public()["why"] or
                                                                                  "сервер не ответил"))
        time.sleep(0.5)
    say("Канал «компьютер — сервер» есть")
    changes = {"phone_relay": True, "phone_relay_host": host, "phone_relay_ssh_port": ssh_port,
               "phone_relay_address": address, "phone_relay_host_key": root.host_key}
    warn = _wait_certificate(root, site, https_port, http_free, changes, say=say, stop=stop, probe_way=probe_way,
                             cert_wait=cert_wait)
    if changes["phone_relay_address"] != address:      # the IP certificate refused: a name of sslip.io
        R.start(R.Config(host=host, ssh_port=ssh_port, host_key=root.host_key,
                         address=changes["phone_relay_address"]), port)
    return {"changes": changes, "address": changes["phone_relay_address"], "warn": warn}


def _wait_certificate(root: Root, site: str, https_port: int, http_free: bool, changes: dict, *, say, stop, probe_way,
                      cert_wait: float) -> str:
    """Waits for the certificate and the phone's way through it; "" — all there, else what is left to the owner."""
    say("Жду сертификат https от Let's Encrypt (обычно до минуты)…")
    started = time.monotonic()
    last_logs = 0.0
    switched = False
    connect_said = False
    while time.monotonic() - started < cert_wait:
        if stop():
            raise SetupError("Остановлено")
        result = probe_way(changes["phone_relay_address"])
        if result == "ok":
            say(f"Путь телефона проверен: {changes['phone_relay_address']} → сервер → этот компьютер")
            return ""
        if result == "other":
            return ("На адресе сервера отвечает не программа — проверьте, что порт " + str(https_port) +
                    " не занят другой программой")
        if result == "connect" and not connect_said and time.monotonic() - started > 30:
            connect_said = True
            say(f"Порт {https_port} сервера пока не отвечает этому компьютеру…")
        if time.monotonic() - last_logs >= LOGS_EVERY:
            last_logs = time.monotonic()
            _code, logs = root.run(S.logs_script(), stop=stop)
            state = S.certificate_of(logs, site)
            if state["state"] == "failed" and state["kind"] == "ip" and S.is_ip(site) and not switched:
                switched = True
                site = S.sslip_name(site)
                changes["phone_relay_address"] = S.address_of(site, https_port)
                say(f"Let's Encrypt не дал сертификат на IP-адрес — беру имя {site} (его DNS ведёт на этот же адрес)")
                code, out = root.run(S.site_script(site, https_port, http_free=http_free), stop=stop)
                if code != 0 or "LB-DONE" not in out:
                    raise SetupError(_failure(out))
                started = time.monotonic()
                continue
            if state["state"] == "failed" and state["kind"] == "firewall":
                return (f"Let's Encrypt не достучался до сервера ({'порт 80' if http_free else 'порт 443'}): откройте "
                        f"порты {'80 и ' if http_free else ''}{https_port} в панели хостинга (брандмауэр, «Firewall») — "
                        "сертификат придёт сам, потом нажмите «Проверить»")
            if state["state"] == "failed" and state["kind"] == "limit":
                return "Let's Encrypt пока не даёт сертификат (лимит запросов) — Caddy попробует сам позже"
        time.sleep(PROBE_EVERY)
    _code, logs = root.run(S.logs_script(), stop=stop)
    state = S.certificate_of(logs, site)
    if state["state"] == "ok":
        return (f"Сертификат есть, но порт {https_port} сервера не отвечает этому компьютеру — откройте его в панели "
                "хостинга (брандмауэр, «Firewall») и нажмите «Проверить»")
    from core.redaction import sanitize_text
    why = sanitize_text(state["why"])[:300]
    return "Сертификат пока не получен" + (f": {why}" if why else " — Caddy продолжает пробовать сам") + \
        ". Нажмите «Проверить» через несколько минут"


def remove(host_text: str, ssh_port, login: str, password: str, *, settings: dict, say, stop) -> dict:
    """Everything of the program taken away from the server; {"changes"} or {"failed": words}."""
    try:
        host = S.clean_host(host_text or settings.get("phone_relay_host") or "")
        ssh_port = int(ssh_port or settings.get("phone_relay_ssh_port") or 22)
    except (S.ServerError, TypeError, ValueError) as exc:
        return {"failed": str(exc) or "Неверный адрес сервера"}
    if not password:
        return {"failed": "Введите пароль сервера"}
    if not R.available():
        return {"failed": "В программе нет модуля paramiko"}
    say(f"Подключаюсь к серверу {host}:{ssh_port}…")
    R.stop()
    try:
        root = Root(host, ssh_port, str(login or "root").strip() or "root", password, say=say)
    except SetupError as exc:
        return {"failed": str(exc)}
    try:
        pinned = str(settings.get("phone_relay_host_key") or "")
        if pinned and not R.same_key(root.host_key, pinned):
            return {"failed": "Ключ сервера не тот, что был при настройке — убирать ничего не стану"}
        say("Убираю с сервера веб-сервер программы, её вход и порты, которые она открыла…")
        code, out = root.run(S.remove_script(), stop=stop)
        if code != 0 or "LB-DONE" not in out:
            return {"failed": "Сервер не закончил уборку — попробуйте ещё раз"}
    except SetupError as exc:
        return {"failed": str(exc)}
    finally:
        root.close()
    R.drop_key()
    say("С сервера всё убрано: VPN и остальное там как было")
    return {"changes": {"phone_relay": False, "phone_relay_address": "", "phone_relay_host_key": ""}}
