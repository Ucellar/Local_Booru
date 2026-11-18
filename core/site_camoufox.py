"""Optional installed Camoufox, isolated from the app's Python and frozen runtime.

One private worker owns the headless browser. Site navigation never uses the user's
desktop or browser profile. Only cookies explicitly supplied by the app are shared.
"""
from __future__ import annotations

import atexit
import base64
from contextlib import contextmanager
import json
import logging
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from core import shutdown
from core.site_camoufox_worker import WORKER, BLOCKER_SCRIPT

MAX_WIRE = 48 * 1024 * 1024
PAGE_WAIT = 45.0
START_WAIT = 35.0
_LOCK = threading.RLock()
_CLIENT = None
_FAILED = ""
_SIGNIN: dict = {}       # explicitly supplied app cookies, retained across a worker restart (memory only)
_LOG = logging.getLogger("local_booru.site_browser")


class Unavailable(RuntimeError):
    """Not installed, disabled, or unable to start: the existing Qt browser may be used."""


class Failed(RuntimeError):
    """An installed browser did not complete a request; do not hide a site's refusal."""


class Cancelled(Failed):
    """The caller no longer needs its browser request."""


def runtime() -> str:
    given = os.environ.get("LOCAL_BOORU_CAMOUFOX_PYTHON", "").strip()
    if given.lower() in ("off", "0", "false"):
        return ""
    if given:
        path = Path(given)
    else:
        path = Path.home() / ".venv-camoufox" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(path) if path.is_file() else ""


def available() -> bool:
    name = runtime()
    return bool(name and name != _FAILED)


def _job(process):
    """Kill the owned Windows worker/Playwright/Firefox tree when its owner closes."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                    ("Flags", wintypes.DWORD), ("Minimum", ctypes.c_size_t), ("Maximum", ctypes.c_size_t),
                    ("Processes", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("Priority", wintypes.DWORD), ("Scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "Read", "Write", "Other")]
    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                    ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle:
        raise OSError("browser job unavailable")
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or \
            not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(process._handle)):
        kernel.CloseHandle(handle)
        raise OSError("browser job unavailable")
    return kernel, handle


class Worker:
    def __init__(self, executable: str, stop_check=None):
        self.process = None
        self.job = None
        self._addon = None
        self.answers = queue.Queue(maxsize=2)
        self.closed = threading.Event()
        self._close_lock = threading.Lock()
        self._disposed = False
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            # Firefox routing skips HTTP redirects. A private session addon enforces the app's network rule
            # before every network request; it never touches the user's installed browser or profile.
            self._addon = tempfile.TemporaryDirectory(prefix="local-booru-browser-", ignore_cleanup_errors=True)
            addon = Path(self._addon.name)
            manifest = {"manifest_version": 2, "name": "Local Booru network rule", "version": "1.0",
                        "browser_specific_settings": {"gecko": {"id": "network-rule@local-booru.local"}},
                        "permissions": ["webRequest", "webRequestBlocking", "<all_urls>"],
                        "background": {"scripts": ["background.js"]}, "incognito": "spanning"}
            (addon / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (addon / "background.js").write_text(BLOCKER_SCRIPT, encoding="utf-8")
            self.process = subprocess.Popen([executable, "-I", "-u", "-c", WORKER, str(addon)],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            **options)
            self.job = _job(self.process)
            self.process.stdin.write(b"start\n")
            self.process.stdin.flush()
            threading.Thread(target=self._read, name="camoufox-replies", daemon=True).start()
            answer = self._answer(START_WAIT, stop_check)
            if not answer.get("ready"):
                raise Failed("Camoufox не запустился")
        except Cancelled:
            self.close()
            raise
        except (OSError, Failed) as exc:
            self.close()
            raise Unavailable("Camoufox не запустился — используется браузер окна программы") from exc

    def _read(self):
        try:
            while not self.closed.is_set():
                line = self.process.stdout.readline(MAX_WIRE + 1)
                if not line or len(line) > MAX_WIRE or not line.endswith(b"\n"):
                    self.answers.put_nowait(None)
                    return
                self.answers.put_nowait(json.loads(line))
        except (OSError, ValueError, queue.Full):
            try:
                self.answers.put_nowait(None)
            except queue.Full:
                self.close()

    def _answer(self, timeout: float, stop_check=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.closed.is_set() or shutdown.is_shutting_down():
                raise Failed("запрос браузера остановлен")
            if stop_check is not None and stop_check():
                raise Cancelled("запрос браузера остановлен")
            try:
                answer = self.answers.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if not isinstance(answer, dict) or not answer.get("ok"):
                raise Failed("Camoufox не получил страницу (сеть, проверка сайта или предел ответа)")
            return answer
        raise Failed("Camoufox не дождался ответа сайта")

    def call(self, action: str, timeout: float = 65.0, stop_check=None, **values):
        packet = json.dumps({"action": action, **values}, ensure_ascii=True).encode("ascii") + b"\n"
        if len(packet) > 1024 * 1024:
            raise Failed("слишком большой запрос браузеру")
        try:
            if self.closed.is_set():
                raise Failed("браузер остановлен")
            self.process.stdin.write(packet)
            self.process.stdin.flush()
            return self._answer(timeout, stop_check)
        except Cancelled:
            self.close()
            raise
        except (OSError, Failed) as exc:
            self.close()
            raise Failed(str(exc) if isinstance(exc, Failed) else "связь с Camoufox прервалась") from exc

    def close(self):
        with self._close_lock:
            if self._disposed:
                return
            self._disposed = True
            self.closed.set()
            process, job, addon = self.process, self.job, self._addon
            self.job = None
            self._addon = None
        if job is not None:
            kernel, handle = job
            kernel.CloseHandle(handle)
        elif process is not None and process.poll() is None:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        if addon is not None:
            addon.cleanup()


def close() -> None:
    global _CLIENT
    client, _CLIENT = _CLIENT, None
    if client is not None:
        client.close()


def _client(stop_check=None):
    global _CLIENT, _FAILED
    name = runtime()
    if not name or name == _FAILED:
        raise Unavailable("Camoufox не установлен или отключён")
    if _CLIENT is None or _CLIENT.closed.is_set():
        try:
            _CLIENT = Worker(name, stop_check)
            for origin, cookies in _SIGNIN.items():
                _CLIENT.call("cookies", origin=origin, cookies=list(cookies.values()), stop_check=stop_check)
            _LOG.info("Camoufox: запущен фоновый браузер сайтов")
        except Unavailable:
            _FAILED = name
            _LOG.warning("Camoufox: запуск не удался; используется браузер окна программы")
            raise
    return _CLIENT


def _url(address: str):
    parsed = urlsplit(address)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise Failed("для браузера нужен адрес HTTP или HTTPS без пароля")
    from core.network import assert_url_allowed
    assert_url_allowed(address)


@contextmanager
def _turn(stop_check=None):
    while True:
        if shutdown.is_shutting_down() or (stop_check is not None and stop_check()):
            raise Cancelled("запрос браузера остановлен")
        if _LOCK.acquire(timeout=0.2):
            break
    try:
        yield
    finally:
        _LOCK.release()


def open_page(url: str, still_check=None, stop_check=None) -> dict:
    _url(url)
    with _turn(stop_check):
        client = _client(stop_check)
        try:
            result = client.call("open", url=url, stop_check=stop_check)
            deadline = time.monotonic() + PAGE_WAIT
            while True:
                try:
                    blocked = bool(result.get("pending") or (still_check and still_check(result["html"], result["url"])))
                except Exception as exc:
                    raise Failed("не удалось проверить полученную страницу") from exc
                if not blocked:
                    if not 0 < int(result.get("status") or 0) < 400:
                        raise Failed(f"сайт ответил HTTP {result['status']}")
                    return result
                if time.monotonic() >= deadline:
                    raise Failed("проверка сайта не завершилась; возможно, требуется участие человека")
                if shutdown.is_shutting_down() or (stop_check is not None and stop_check()):
                    raise Cancelled("запрос браузера остановлен")
                time.sleep(0.25)
                result = client.call("snapshot", timeout=15.0, stop_check=stop_check)
        except Failed:
            # Do not leave a failed page refreshing or running its verification scripts.
            client.close()
            raise


def fetch_bytes(url: str, referer: str = "", limit: int = 0) -> tuple:
    _url(url)
    if referer:
        _url(referer)
    with _turn():
        result = _client().call("bytes", url=url, referer=referer, limit=int(limit or 0))
    try:
        body = base64.b64decode(result["data"], validate=True)
    except (ValueError, KeyError) as exc:
        raise Failed("браузер прислал повреждённый ответ") from exc
    if len(body) > min(32 * 1024 * 1024, limit or 32 * 1024 * 1024):
        raise Failed("браузер прислал слишком большой ответ")
    return int(result["status"]), body, str(result.get("type") or "")


def share_cookies(origin: str, cookies: list, *, forget: bool = False) -> int:
    _url(origin)
    with _turn():
        host = urlsplit(origin).hostname.lower()
        accepted = [dict(cookie) for cookie in cookies if cookie.get("name") and
                    (host == str(cookie.get("domain") or host).lower().lstrip(".") or
                     host.endswith("." + str(cookie.get("domain") or host).lower().lstrip(".")))]
        result = int(_client().call("cookies", origin=origin, cookies=accepted, forget=forget)["count"])
        kept = _SIGNIN.setdefault(origin, {})
        for cookie in accepted:
            key = (cookie["name"], cookie.get("domain") or host, cookie.get("path") or "/")
            expires = cookie.get("expires", cookie.get("expirationDate"))
            expired = isinstance(expires, (int, float)) and 0 < expires <= time.time()
            if forget or expired:
                kept.pop(key, None)
            else:
                kept[key] = cookie
        return result


shutdown.register("Camoufox", close)
atexit.register(close)
