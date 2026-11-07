"""(v5.44.71) What the program does on the owner's server (``core/phone_relay_setup.py`` runs it as root, once): the
scripts and the web server's configuration — everything else of the server is not touched (Amnezia's containers,
the SSH daemon's own settings, its users).

* :func:`probe_script` — only looks: is it root, Docker, which TCP ports are taken (Amnezia's protocols often sit on
  443), the firewall, our own container of before;
* :func:`apply_script` — the user :data:`phone_relay.USER` with this computer's key only
  (:func:`authorized_line`: ``restrict`` — no shell, no terminal, no agent; ``port-forwarding`` with
  ``permitlisten`` — one port on 127.0.0.1, ``permitopen`` — none to go out; the forced command — :func:`takeover_text`,
  which closes this computer's own older connections), the web server — Caddy in Docker
  (``local-booru-relay``, network of the host, ``--restart unless-stopped``) with :func:`caddyfile`, the firewall's
  ports when ufw or firewalld is on (what it opened is written in ``/opt/local-booru/opened`` — for the removal);
* :func:`logs_script` — what Caddy says (the certificate: :func:`certificate_of`);
* :func:`site_script` — another address of the site (the IP certificate refused — a name of sslip.io);
* :func:`remove_script` — all of it away again.

The certificate: an IP address gets Let's Encrypt's six-day one (profile ``shortlived``; generally available since
15.01.2026; Caddy renews it itself), a name — the usual one.  Let's Encrypt checks the server on port 80 (HTTP-01) or
443 (TLS-ALPN-01) — one of them must be free.
"""
from __future__ import annotations

import ipaddress
import json
import re

from core.phone_relay import REMOTE_PORT, USER

CONTAINER = "local-booru-relay"
IMAGE = "caddy:2"
FOLDER = "/opt/local-booru"
TAKEOVER = "/usr/local/bin/local-booru-takeover"
HTTPS_PORTS = (8443, 2053, 2083, 2087, 2096, 9443)          # the first one free is taken; 443 is left to the VPN
#   (Amnezia's XRay and Cloak want it — the owner may switch to them), unless port 80 is taken: then Let's Encrypt
#   checks the server on 443 only
KEY_LINE = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,3} local-booru")
HOST_NAME = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


class ServerError(Exception):
    """Said to the person as it is."""


# ── what is asked ───────────────────────────────────────────────────────────────────────────────────────────────
def clean_host(text: str) -> str:
    """The server's address as typed («203.0.113.5», «vpn.example.com», even «ssh root@203.0.113.5» or a link) → the
    address; ServerError — not one."""
    text = str(text or "").strip()
    text = re.sub(r"^(?:ssh\s+)?(?:[a-z][a-z0-9+.-]*://)?(?:[^@/\s]+@)?", "", text, flags=re.I)
    text = text.split("/")[0].strip().strip("[]")
    if re.fullmatch(r"[0-9.]+:\d+", text):
        text = text.rsplit(":", 1)[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    if HOST_NAME.fullmatch(text.lower().rstrip(".")):
        return text.lower().rstrip(".")
    raise ServerError("Это не адрес сервера — нужен его IP (как в Amnezia: четыре числа через точку) или имя")


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(str(host))
    except ValueError:
        return False
    return True


def sslip_name(ip: str) -> str:
    """The name of sslip.io that leads to ``ip``: «203-0-113-5.sslip.io» (its DNS answers with the address itself)."""
    address = ipaddress.ip_address(ip)
    text = address.exploded.replace(":", "-") if address.version == 6 else str(address).replace(".", "-")
    return f"{text}.sslip.io"


def address_of(site: str, https_port: int) -> str:
    """The phone's address: «https://203.0.113.5:8443», «https://203-0-113-5.sslip.io»."""
    host = f"[{site}]" if is_ip(site) and ":" in site else site
    return f"https://{host}" + ("" if int(https_port) == 443 else f":{int(https_port)}")


# ── the key of this computer ────────────────────────────────────────────────────────────────────────────────────
def authorized_line(public_line: str, *, listen: bool = True) -> str:
    """The line of ``authorized_keys``: the key may only open :data:`REMOTE_PORT` on 127.0.0.1 of the server and run
    :data:`TAKEOVER` (``listen`` — the server's OpenSSH knows ``permitlisten``: 7.8 and newer)."""
    if not KEY_LINE.fullmatch(str(public_line or "")):
        raise ServerError("Ключ программы испорчен")
    options = ["restrict", "port-forwarding"]
    if listen:
        options.append(f'permitlisten="127.0.0.1:{REMOTE_PORT}"')
    options += ['permitopen="127.0.0.1:9"', f'command="{TAKEOVER}"']
    return ",".join(options) + " " + public_line


def openssh_version(banner: str) -> tuple:
    """«SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.10» → (8, 9); () — not OpenSSH."""
    found = re.search(r"OpenSSH_(\d+)\.(\d+)", str(banner or ""))
    return (int(found.group(1)), int(found.group(2))) if found else ()


def takeover_text() -> str:
    """The key's forced command: this computer's older connections the server still holds (a computer that slept, a
    network that changed: the server keeps a dead connection — and its port — for hours) are closed; only the
    connections of :data:`USER`, never the ones it runs in."""
    return f"""#!/bin/sh
# Local Booru: this computer connected again. Its older connections (one that died in sleep or when the network
# changed) still hold the port on this server: they are closed. Only the connections of the user {USER}, never the
# one this runs in.
mine=" "
p=$$
while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; do
  mine="$mine$p "
  p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
done
for q in $(pgrep -u {USER} -x 'sshd|sshd-session' 2>/dev/null); do
  case "$mine" in *" $q "*) ;; *) kill "$q" 2>/dev/null ;; esac
done
exit 0
"""


# ── the web server ──────────────────────────────────────────────────────────────────────────────────────────────
DOWN_PAGE = ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
             'initial-scale=1"><meta name="theme-color" content="#0d0d0d"><title>Компьютер не отвечает — Local Booru</title>'
             '</head><body style="margin:0;padding:56px 22px;background:#1f1f1f;color:#d9d9d9;font:16px/1.5 system-ui,'
             '-apple-system,Roboto,sans-serif"><h1 style="margin:0 0 12px;color:#fff;font-size:24px">Компьютер не '
             'отвечает</h1><p>Библиотека живёт на компьютере. Проверьте: компьютер включён и не спит, Local Booru на нём '
             'запущена, в «Настройки → Телефон» связь через сервер включена.</p><p><a style="color:#ff5c80" href="/m/">'
             'Попробовать снова</a></p></body></html>')   # no braces: Caddy would take them for its placeholders


def caddyfile(site: str, https_port: int, *, http_free: bool) -> str:
    """Caddy's configuration: https://<site>[:port] → 127.0.0.1::data:`REMOTE_PORT` (the connection to the
    program), with Let's Encrypt's certificate — an IP: the six-day one; ``http_free`` — port 80 is free (HTTP-01 and
    the redirect from http://), else the check goes on 443 (TLS-ALPN-01).  The program not there: «Компьютер не
    отвечает» (the active check of the way every 10 s: a computer asleep is known at once, not after a long wait)."""
    https_port = int(https_port)
    if not http_free and https_port != 443:
        raise ServerError("Нужен свободный порт 80 или 443")
    ip = is_ip(site)
    head = ["{", "\tadmin off"]
    if https_port != 443:
        head.append(f"\thttps_port {https_port}")
    if not http_free:
        head.append("\tauto_https disable_redirects")
    if ip:
        head.append(f"\tdefault_sni {site}")
    head += ["}", ""]
    issuer = []
    if ip:
        issuer.append("\t\t\tprofile shortlived")
    if not http_free:
        issuer.append("\t\t\tdisable_http_challenge")
    if https_port != 443:
        issuer.append("\t\t\tdisable_tlsalpn_challenge")
    tls = ["\ttls {", "\t\tissuer acme {", *issuer, "\t\t}", "\t}"] if issuer else []
    origin = address_of(site, https_port).removeprefix("https://")
    body = [f"{address_of(site, https_port)} {{", *tls,
            f"\treverse_proxy 127.0.0.1:{REMOTE_PORT} {{",
            "\t\thealth_uri /m/alive",
            "\t\thealth_interval 10s",
            "\t\thealth_timeout 5s",
            "\t\thealth_status 2xx",
            "\t\thealth_headers {",
            f"\t\t\tHost {origin}",
            "\t\t}",
            "\t\tflush_interval -1",
            "\t\ttransport http {",
            "\t\t\tdial_timeout 5s",
            "\t\t}",
            "\t}",
            "\thandle_errors 5xx {",
            "\t\theader Content-Type \"text/html; charset=utf-8\"",
            "\t\theader Cache-Control no-store",
            "\t\trespond <<HTML",
            "\t\t\t" + DOWN_PAGE,
            "\t\t\tHTML",
            "\t}",
            "}", ""]
    return "\n".join(head + body)


# ── the scripts ─────────────────────────────────────────────────────────────────────────────────────────────────
def probe_script() -> str:
    """Looks only: «LB-…» lines for :func:`read_probe`."""
    return f"""set -u
echo "LB-ID $(id -u)"
echo "LB-OS $( (. /etc/os-release 2>/dev/null; echo "${{PRETTY_NAME:-unknown}}") )"
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then echo "LB-DOCKER ok"; else echo "LB-DOCKER down"; fi
  echo "LB-OURS $(docker ps -a --filter 'name=^/{CONTAINER}$' --format '{{{{.Status}}}}' 2>/dev/null | head -n 1)"
else
  echo "LB-DOCKER none"
fi
if command -v ss >/dev/null 2>&1; then
  ss -Hltnp 2>/dev/null | awk '{{print "LB-LISTEN", $4, $NF}}'
elif command -v netstat >/dev/null 2>&1; then
  netstat -ltnp 2>/dev/null | awk 'NR > 2 {{print "LB-LISTEN", $4, $7}}'
elif [ -r /proc/net/tcp ]; then
  for f in /proc/net/tcp /proc/net/tcp6; do
    [ -r "$f" ] || continue
    awk 'NR > 1 && $4 == "0A" {{n = split($2, a, ":"); print a[n]}}' "$f" | while read -r hex; do
      echo "LB-LISTEN *:$(printf '%d' "0x$hex") ?"
    done
  done
else
  echo "LB-LISTEN unknown"
fi
if command -v ufw >/dev/null 2>&1; then echo "LB-UFW $(ufw status 2>/dev/null | head -n 1)"; fi
if command -v firewall-cmd >/dev/null 2>&1; then echo "LB-FIREWALLD $(firewall-cmd --state 2>/dev/null)"; fi
for tool in pgrep ps useradd; do command -v $tool >/dev/null 2>&1 || echo "LB-MISSING $tool"; done
if command -v sshd >/dev/null 2>&1; then
  sshd -T -C user={USER},host=localhost,addr=127.0.0.1 2>/dev/null \
    | grep -iE '^(allowusers|allowgroups|denyusers|denygroups|allowtcpforwarding|disableforwarding|pubkeyauthentication) ' \
    | sed 's/^/LB-SSHD /'
fi
echo "LB-END"
"""


def read_probe(text: str) -> dict:
    """What :func:`probe_script` said: {"root", "os", "docker", "ours", "taken": {port: who}, "ufw", "firewalld",
    "missing", "complete"}."""
    out = {"root": False, "os": "", "docker": "", "ours": "", "taken": {}, "ufw": False, "firewalld": False,
           "missing": [], "complete": False, "ports_known": True, "sshd": {}}
    for line in str(text or "").splitlines():
        kind, _, rest = line.strip().partition(" ")
        if kind == "LB-ID":
            out["root"] = rest.strip() == "0"
        elif kind == "LB-OS":
            out["os"] = rest.strip()[:80]
        elif kind == "LB-DOCKER":
            out["docker"] = rest.strip()
        elif kind == "LB-OURS":
            out["ours"] = rest.strip()
        elif kind == "LB-LISTEN":
            if rest.strip() == "unknown":
                out["ports_known"] = False
                continue
            where, _, who = rest.strip().partition(" ")
            port = where.rsplit(":", 1)[-1]
            if port.isdigit():
                found = re.search(r'"([^"]+)"', who)
                out["taken"].setdefault(int(port), found.group(1) if found else who[:40])
        elif kind == "LB-UFW":
            out["ufw"] = "active" in rest.lower() and "inactive" not in rest.lower()
        elif kind == "LB-FIREWALLD":
            out["firewalld"] = rest.strip() == "running"
        elif kind == "LB-MISSING":
            out["missing"].append(rest.strip())
        elif kind == "LB-SSHD":
            name, _, value = rest.strip().partition(" ")
            out["sshd"].setdefault(name.lower(), []).append(value.strip())
        elif kind == "LB-END":
            out["complete"] = True
    return out


def choose_ports(probe: dict) -> tuple:
    """(the HTTPS port, port 80 free) from what the server listens on; ServerError — none fits.  Port 80 free — the
    first free of :data:`HTTPS_PORTS` (443 stays the VPN's); taken — 443 (Let's Encrypt checks on it then)."""
    taken = probe.get("taken") or {}
    if 80 not in taken:
        https = next((p for p in HTTPS_PORTS if p not in taken), None)
        if https is None:
            raise ServerError("На сервере заняты все порты, которые подходят для https (8443, 2053, 2083, 2087, 2096, "
                              "9443)")
        return https, True
    if 443 not in taken:
        return 443, False
    raise ServerError(f"На сервере заняты порт 80 ({taken.get(80) or 'другая программа'}) и 443 "
                      f"({taken.get(443) or 'другая программа'}): Let's Encrypt проверяет сервер на одном из них, "
                      "прежде чем дать сертификат. Освободите один из этих портов")


def sshd_refusal(probe: dict) -> str:
    """Why the server's SSH would not let the program's own user keep its port ("" — it will): its own rules, as
    ``sshd -T`` says them for that user (the program never changes them)."""
    rules = probe.get("sshd") or {}

    def first(name: str) -> str:
        values = rules.get(name) or [""]
        return values[0].lower()
    if first("allowtcpforwarding") in ("no", "local") or first("disableforwarding") == "yes":
        return ("SSH сервера запрещает перенаправлять порты (AllowTcpForwarding) — без этого канала не будет. Разрешите "
                f"его для пользователя {USER} в /etc/ssh/sshd_config")
    if first("pubkeyauthentication") == "no":
        return "SSH сервера не пускает по ключам (PubkeyAuthentication no) — программе нужен вход по ключу"
    allow = " ".join(rules.get("allowusers") or []).split()
    if allow and not any(user.split("@")[0] in (USER, "*") for user in allow):
        return f"В SSH сервера стоит AllowUsers — добавьте туда пользователя {USER}, иначе программа не войдёт"
    deny = " ".join(rules.get("denyusers") or []).split()
    if any(user.split("@")[0] in (USER, "*") for user in deny):
        return f"В SSH сервера стоит DenyUsers, и он не пускает {USER}"
    return ""


def apply_script(*, public_line: str, listen: bool, site: str, https_port: int, http_free: bool, ufw: bool,
                 firewalld: bool) -> str:
    """Puts it all on the server: the user and its key, the forced command, Caddy, the firewall's ports.  Prints
    «LB-STEP <what>» as it goes, «LB-FAIL <what> <why>» and stops on a failure, «LB-DONE» at the end."""
    key = authorized_line(public_line, listen=listen)
    config = caddyfile(site, https_port, http_free=http_free)
    ports = [f"{int(https_port)}/tcp"] + (["80/tcp"] if http_free else [])
    firewall = []
    if ufw:
        for port in ports:
            firewall.append(f'if ! ufw status | grep -qE "^{port}[[:space:]]"; then ufw allow {port} >/dev/null && '
                            f'echo "ufw {port}" >> {FOLDER}/opened; fi')
    if firewalld:
        for port in ports:
            firewall.append(f'if ! firewall-cmd --query-port={port} >/dev/null 2>&1; then '
                            f'firewall-cmd --permanent --add-port={port} >/dev/null && firewall-cmd --add-port={port} '
                            f'>/dev/null && echo "firewalld {port}" >> {FOLDER}/opened; fi')
    return f"""set -u
fail() {{ echo "LB-FAIL $1 ${{2:-}}"; exit 1; }}
step() {{ echo "LB-STEP $1"; }}
step user
if ! id -u {USER} >/dev/null 2>&1; then
  useradd --create-home --home-dir /home/{USER} --shell /bin/sh {USER} >/dev/null 2>&1 || fail useradd
fi
usermod -s /bin/sh -p '*' {USER} >/dev/null 2>&1 || fail usermod
home=$(getent passwd {USER} | cut -d: -f6)
[ -n "$home" ] && [ -d "$home" ] || fail home
chown {USER}: "$home" && chmod go-w "$home" || fail home
install -d -m 700 -o {USER} -g "$(id -gn {USER})" "$home/.ssh" || fail ssh-dir
umask 077
cat > "$home/.ssh/authorized_keys.lb" <<'LBKEY'
{key}
LBKEY
chown {USER}: "$home/.ssh/authorized_keys.lb" && chmod 600 "$home/.ssh/authorized_keys.lb" || fail keys
mv -f "$home/.ssh/authorized_keys.lb" "$home/.ssh/authorized_keys" || fail keys
umask 022
step takeover
cat > {TAKEOVER}.lb <<'LBTAKE'
{takeover_text()}LBTAKE
chmod 755 {TAKEOVER}.lb && mv -f {TAKEOVER}.lb {TAKEOVER} || fail takeover
step caddy
install -d -m 755 {FOLDER} || fail folder
cat > {FOLDER}/Caddyfile <<'LBCADDY'
{config}LBCADDY
chmod 644 {FOLDER}/Caddyfile || fail folder
step pull
docker pull {IMAGE} >/dev/null 2>&1 || docker image inspect {IMAGE} >/dev/null 2>&1 || fail pull
docker rm -f {CONTAINER} >/dev/null 2>&1
step run
docker run -d --name {CONTAINER} --restart unless-stopped --network host \\
  -v {FOLDER}/Caddyfile:/etc/caddy/Caddyfile:ro \\
  -v local-booru-caddy-data:/data -v local-booru-caddy-config:/config {IMAGE} >/dev/null 2>&1 || fail run
step firewall
touch {FOLDER}/opened
{chr(10).join(firewall)}
sleep 3
if ! docker ps --filter 'name=^/{CONTAINER}$' --format '{{{{.Status}}}}' | grep -q '^Up'; then
  docker logs --tail 15 {CONTAINER} 2>&1 | sed 's/^/LB-LOG /'
  fail started
fi
echo "LB-DONE"
"""


def logs_script(minutes: int = 20) -> str:
    return f"""docker logs --since {int(minutes)}m {CONTAINER} 2>&1 | tail -n 400
echo "LB-STATUS $(docker ps -a --filter 'name=^/{CONTAINER}$' --format '{{{{.Status}}}}' | head -n 1)"
"""


def site_script(site: str, https_port: int, *, http_free: bool) -> str:
    """Another address of the site (and its certificate): the configuration written anew, Caddy started again."""
    return f"""set -u
cat > {FOLDER}/Caddyfile <<'LBCADDY'
{caddyfile(site, https_port, http_free=http_free)}LBCADDY
docker restart {CONTAINER} >/dev/null 2>&1 || {{ echo "LB-FAIL restart"; exit 1; }}
echo "LB-DONE"
"""


def remove_script() -> str:
    """All of it away: the container and its certificates, the firewall's ports it opened, the forced command, the
    user (its connections closed first), the folder."""
    return f"""set -u
docker rm -f {CONTAINER} >/dev/null 2>&1
docker volume rm local-booru-caddy-data local-booru-caddy-config >/dev/null 2>&1
if [ -f {FOLDER}/opened ]; then
  while read -r tool port; do
    case "$tool" in
      ufw) ufw delete allow "$port" >/dev/null 2>&1 ;;
      firewalld) firewall-cmd --permanent --remove-port="$port" >/dev/null 2>&1; firewall-cmd --remove-port="$port" >/dev/null 2>&1 ;;
    esac
  done < {FOLDER}/opened
fi
rm -f {TAKEOVER}
if id -u {USER} >/dev/null 2>&1; then
  pkill -u {USER} >/dev/null 2>&1
  sleep 1
  userdel -r {USER} >/dev/null 2>&1 || userdel {USER} >/dev/null 2>&1
fi
rm -rf {FOLDER}
echo "LB-DONE"
"""


# ── what Caddy says of the certificate ──────────────────────────────────────────────────────────────────────────
def certificate_of(logs: str, site: str) -> dict:
    """{"state": "ok" | "waiting" | "failed", "kind": "ip" | "firewall" | "limit" | "other" | "", "why"} — from Caddy's
    journal (JSON lines of «tls.obtain» / «tls.issuance.acme»)."""
    state = {"state": "waiting", "kind": "", "why": ""}
    for line in str(logs or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or str(entry.get("identifier") or site) != site:
            continue
        message = str(entry.get("msg") or "")
        if "certificate obtained successfully" in message or "certificate already exists" in message:
            state = {"state": "ok", "kind": "", "why": ""}
        elif entry.get("level") == "error" and ("could not get certificate" in message or "obtain" in message
                                                or "validating authorization" in message):
            error = str(entry.get("error") or message)
            low = error.lower()
            if any(word in low for word in ("rejectedidentifier", "unsupportedidentifier", "invalidprofile", "profile",
                                            "ip address identifiers", "invalid identifiers")):
                kind = "ip"
            elif any(word in low for word in ("timeout during connect", "connection refused", "firewall",
                                              "connection reset", "no route", "urn:ietf:params:acme:error:connection")):
                kind = "firewall"
            elif "ratelimited" in low or "too many" in low:
                kind = "limit"
            else:
                kind = "other"
            state = {"state": "failed", "kind": kind, "why": error[:400]}
    return state
