"""Local web interface of Local Booru: gallery, posts, tags and manga as web pages.

It runs inside the desktop program (shown in its window) or alone (``python -m webui``) and can be
opened in any browser on this computer.

Safety:
* listens on 127.0.0.1 only;
* every request needs the session cookie that ``/auth?t=<token>`` sets — the token is made anew for
  every run and handed to the program window / browser by the program itself;
* the ``Host`` header must name this server (a web page cannot reach it through a DNS trick);
* actions that change the library are POST requests whose ``Origin`` must be this server, and the
  cookie is ``SameSite=Strict``, so another site open in the same browser cannot press them.
"""
from __future__ import annotations

import contextlib
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import selectors
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urlparse, urlsplit

_LOG = logging.getLogger("local_booru.webui")
STATIC_DIR = Path(__file__).with_name("static")
COOKIE = "lb_session"
WORKERS = 16              # requests served at once
REQUEST_TIMEOUT = 10      # seconds a request may wait for a stalled browser (reading it or sending the answer)
KEEPALIVE_IDLE = 60       # seconds an idle browser connection stays open; it holds no worker meanwhile
MAX_IDLE_CONNECTIONS = 256
MAX_BODY = 10 * 1024 * 1024
MAX_UPLOAD = 200 * 1024 * 1024        # a saved page sent to «Описание с сайта» / «Скачать с сайта» (a .zip with its pictures)
UPLOAD_PATHS = ("/meta-file", "/find-file")    # the ends of the addresses that take such a file as the body
INT_LIMIT = 10 ** 9       # numbers in addresses (page, position) are clamped to this
# Pages load scripts, styles and pictures only from this server; text from the library (tag names,
# file names) is escaped anyway, this is the second lock on the door.
CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")


class _Conn:
    """A browser connection; between its requests it waits in the selector, not in a worker."""

    __slots__ = ("sock", "address", "handler", "since")

    def __init__(self, sock, address):
        self.sock, self.address, self.handler, self.since = sock, address, None, time.monotonic()


class _PooledHTTPServer(HTTPServer):
    """HTTP server whose fixed worker threads serve requests, not connections.

    A thread per request (``ThreadingHTTPServer``) would open a new SQLite connection for every
    thumbnail; fixed workers keep their connections warm.  A browser keeps its connections open
    between requests: those wait in one selector thread, and a worker takes a connection only when
    a request has arrived on it.  So idle connections — a page left open in another browser, a
    program opening sockets and sending nothing — never take the workers away from the pages.
    """

    allow_reuse_address = False
    request_queue_size = 64       # connections waiting to be accepted (5 by default: a burst lost some for a second)

    def __init__(self, address, handler, workers: int = WORKERS):
        super().__init__(address, handler)
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._open: set = set()                 # every connection socket, served or waiting
        self._incoming: list = []               # connections handed to the selector thread
        self._stopping = False
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._workers = [threading.Thread(target=self._work, name=f"webui-worker-{n}", daemon=True)
                         for n in range(max(1, int(workers)))]
        for worker in self._workers:
            worker.start()
        self._watcher = threading.Thread(target=self._watch_idle, name="webui-idle", daemon=True)
        self._watcher.start()

    # ── connections ───────────────────────────────────────────────────────
    def process_request(self, request, client_address):
        with self._lock:
            self._open.add(request)
        self._wait_for_request(_Conn(request, client_address))

    def _wait_for_request(self, conn: _Conn) -> None:
        conn.since = time.monotonic()
        with self._lock:
            self._incoming.append(conn)
        self._wake()

    def _wake(self) -> None:
        try:
            self._wake_w.send(b"x")
        except OSError:   # the wake-up pipe is full: the selector is waking up anyway
            pass

    def _watch_idle(self) -> None:
        selector = selectors.DefaultSelector()
        selector.register(self._wake_r, selectors.EVENT_READ, None)
        idle: dict = {}                         # socket -> _Conn
        try:
            while not self._stopping:
                try:
                    events = selector.select(timeout=1.0)
                except (OSError, ValueError) as exc:   # a socket closed under the selector
                    _LOG.debug("idle selector: %s", exc)
                    events = []
                    for sock in list(idle):
                        if sock.fileno() < 0:
                            with contextlib.suppress(KeyError, ValueError, OSError):
                                selector.unregister(sock)
                            self._close(idle.pop(sock))
                for key, _mask in events:
                    if key.data is None:
                        with contextlib.suppress(OSError):
                            while self._wake_r.recv(4096):
                                pass
                        continue
                    conn = key.data
                    selector.unregister(conn.sock)
                    idle.pop(conn.sock, None)
                    self._jobs.put(conn)            # a request has arrived: a worker serves it
                with self._lock:
                    fresh, self._incoming = self._incoming, []
                for conn in fresh:
                    try:
                        selector.register(conn.sock, selectors.EVENT_READ, conn)
                        idle[conn.sock] = conn
                    except (KeyError, ValueError, OSError):
                        self._close(conn)
                now = time.monotonic()
                expired = [c for c in idle.values() if now - c.since > KEEPALIVE_IDLE]
                if len(idle) - len(expired) > MAX_IDLE_CONNECTIONS:
                    rest = sorted((c for c in idle.values() if c not in expired), key=lambda c: c.since)
                    expired += rest[:len(rest) - MAX_IDLE_CONNECTIONS]
                for conn in expired:
                    with contextlib.suppress(KeyError, ValueError, OSError):
                        selector.unregister(conn.sock)
                    idle.pop(conn.sock, None)
                    self._close(conn)
        finally:
            for conn in list(idle.values()):
                self._close(conn)
            selector.close()

    def _work(self) -> None:
        while True:
            conn = self._jobs.get()
            if conn is None:
                return
            keep = False
            try:
                if conn.handler is None:
                    conn.handler = self.RequestHandlerClass(conn.sock, conn.address, self)   # the first request
                else:
                    conn.handler.next_request()
                keep = not conn.handler.close_connection
            except Exception:  # one broken connection must not stop the worker
                self.handle_error(conn.sock, conn.address)
            if keep and not self._stopping:
                self._wait_for_request(conn)
            else:
                self._close(conn)

    def _close(self, conn: _Conn) -> None:
        if conn.handler is not None:
            conn.handler.close_files()
        with self._lock:
            self._open.discard(conn.sock)
        self.shutdown_request(conn.sock)

    def handle_error(self, request, client_address):
        _LOG.debug("web connection %s failed:\n%s", client_address, traceback.format_exc())

    def close_all(self) -> None:
        self._stopping = True
        for _ in self._workers:
            self._jobs.put(None)
        self._wake()
        with self._lock:
            sockets = list(self._open)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # already closed by the browser
        self._watcher.join(timeout=3)
        for sock in (self._wake_r, self._wake_w):
            sock.close()


class _FileStreams:
    """Files being sent to the browser right now.

    On Windows an open file can be neither moved nor deleted, and a ``<video>`` keeps its file
    open while it plays.  Before the program moves a card's file (trash, restore) it calls
    ``holding(path)``: streams of that file are cut, and new requests for it wait until the move
    is over.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._streams: dict = {}                # key -> {socket: threading.Event (set when the file is closed)}
        self._held: dict = {}                   # key -> how many moves hold it

    @staticmethod
    def key(path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def held(self, path) -> bool:
        with self._lock:
            return self._held.get(self.key(path), 0) > 0

    @contextlib.contextmanager
    def streaming(self, path, sock):
        key, closed = self.key(path), threading.Event()
        with self._lock:
            self._streams.setdefault(key, {})[sock] = closed
        try:
            yield
        finally:
            closed.set()
            with self._lock:
                streams = self._streams.get(key, {})
                streams.pop(sock, None)
                if not streams:
                    self._streams.pop(key, None)

    def cut(self, path=None, wait: float = 2.0) -> int:
        """Stop sending ``path`` (every file when None); returns how many streams were cut."""
        with self._lock:
            if path is None:
                streams = [(sock, ev) for group in self._streams.values() for sock, ev in group.items()]
            else:
                streams = list(self._streams.get(self.key(path), {}).items())
        for sock, _closed in streams:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
        # the program's own player (a video played inside a page) lets go of the file too
        from webui.video_relay import RELAY
        RELAY.release(None if path is None else self.key(path), wait)
        deadline = time.monotonic() + wait
        for _sock, closed in streams:
            closed.wait(max(0.0, deadline - time.monotonic()))
        return len(streams)

    @contextlib.contextmanager
    def holding(self, *paths):
        keys = [self.key(p) for p in paths if p]
        with self._lock:
            for key in keys:
                self._held[key] = self._held.get(key, 0) + 1
        try:
            for path in paths:
                if path:
                    self.cut(path)
            yield
        finally:
            with self._lock:
                for key in keys:
                    left = self._held.get(key, 0) - 1
                    if left > 0:
                        self._held[key] = left
                    else:
                        self._held.pop(key, None)


FILE_STREAMS = _FileStreams()


@dataclass
class Request:
    method: str
    path: str
    params: dict                       # values captured by the route pattern
    query: dict                        # ?a=1&a=2 -> {"a": ["1", "2"]}
    headers: dict
    body: bytes = b""
    settings: dict = field(default_factory=dict)
    server: "WebUI | None" = None

    def arg(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return str(values[0]) if values else default

    def int_arg(self, name: str, default: int = 0, lo: int | None = None, hi: int | None = None) -> int:
        try:
            value = int(self.arg(name, str(default)) or default)
        except ValueError:
            value = default
        value = max(-INT_LIMIT, min(INT_LIMIT, value))   # SQLite takes 64-bit numbers only
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return value

    def json(self) -> dict:
        try:
            data = json.loads(self.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: dict = field(default_factory=dict)
    file: Path | None = None           # stream this file instead of ``body`` (Range supported)
    stream: object = None              # an iterator of bytes sent as they come (no length; the connection closes)

    @staticmethod
    def html(text: str, status: int = 200) -> "Response":
        return Response(status, text.encode("utf-8"))

    @staticmethod
    def json(data, status: int = 200) -> "Response":
        return Response(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    @staticmethod
    def redirect(location: str, status: int = 303) -> "Response":
        return Response(status, b"", headers={"Location": location})

    @staticmethod
    def not_found(text: str = "Не найдено") -> "Response":
        return Response(404, text.encode("utf-8"), "text/plain; charset=utf-8")


Handler = Callable[[Request], Response]


class Router:
    def __init__(self):
        self._routes: list[tuple[str, re.Pattern, Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self._routes.append((method.upper(), re.compile("^" + pattern + "$"), handler))

    def get(self, pattern: str):
        def deco(fn: Handler) -> Handler:
            self.add("GET", pattern, fn)
            return fn
        return deco

    def post(self, pattern: str):
        def deco(fn: Handler) -> Handler:
            self.add("POST", pattern, fn)
            return fn
        return deco

    def match(self, method: str, path: str):
        allowed = False
        for route_method, pattern, handler in self._routes:
            m = pattern.match(path)
            if not m:
                continue
            if route_method == method:
                return handler, m.groupdict()
            allowed = True
        return (None, "method") if allowed else (None, None)


class WebUI:
    """The server.  ``settings_provider`` returns the program's current settings dict."""

    def __init__(self, settings_provider: Callable[[], dict], *, host: str = "127.0.0.1", port: int = 0):
        self.settings_provider = settings_provider
        self.host = host
        self.port = int(port or 0)
        self.token = secrets.token_urlsafe(32)
        self.router = Router()
        self._httpd: _PooledHTTPServer | None = None
        self._thread: threading.Thread | None = None
        from webui import pages
        pages.register(self.router)

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> str:
        if self._httpd is not None:
            return self.base_url
        server = self

        class _Handler(_RequestHandler):
            webui = server

        httpd = _PooledHTTPServer((self.host, self.port), _Handler)
        self.port = int(httpd.server_address[1])
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="webui-http", daemon=True)
        self._thread.start()
        _LOG.info("web interface on %s", self.base_url)
        return self.base_url

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.close_all()
        self._httpd.server_close()
        self._httpd = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def login_url(self, next_path: str = "/posts") -> str:
        """The address the program opens: sets the session cookie, then shows ``next_path``."""
        return f"{self.base_url}/auth?t={quote(self.token)}&next={quote(next_path)}"

    def settings(self) -> dict:
        try:
            return dict(self.settings_provider() or {})
        except Exception as exc:  # the provider belongs to the program; a page must still render
            _LOG.warning("settings provider failed: %s", exc)
            return {}

    # ── request checks ──────────────────────────────────────────────────────
    def host_ok(self, host_header: str) -> bool:
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        return str(host_header or "").strip().lower() in allowed

    def origin_ok(self, origin: str) -> bool:
        origin = str(origin or "").strip().lower()
        return origin in {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

    def cookie_ok(self, cookie_header: str) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header or "")
        except Exception:  # a malformed Cookie header is simply "not logged in"
            return False
        morsel = cookie.get(COOKIE)
        return bool(morsel) and secrets.compare_digest(morsel.value, self.token)


class _RequestHandler(BaseHTTPRequestHandler):
    """One browser connection.  It serves one request per call; the server keeps the connection
    (with this object and its buffers) between requests and calls ``next_request`` for the next one."""

    webui: WebUI
    protocol_version = "HTTP/1.1"
    server_version = "LocalBooru"
    timeout = REQUEST_TIMEOUT
    # Headers and body go out as two writes; with Nagle's algorithm the second waits for the
    # browser's delayed acknowledgement (40 ms on Linux, up to 200 ms on Windows) on every request
    # of a kept-alive connection — every thumbnail after the first few.
    disable_nagle_algorithm = True

    def handle(self):
        self.next_request()

    def next_request(self) -> None:
        self.close_connection = True
        self.handle_one_request()

    def finish(self):
        # Only flush: the connection stays open for the next request; the server closes it.
        if not self.wfile.closed:
            try:
                self.wfile.flush()
            except OSError:
                self.close_connection = True

    def close_files(self) -> None:
        for stream in (self.wfile, self.rfile):
            with contextlib.suppress(OSError):
                stream.close()

    def log_message(self, fmt, *args):  # quiet: the program log is not an HTTP access log
        _LOG.debug("%s %s", self.address_string(), _loggable(fmt % args))

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def _refuse(self, response: Response, method: str) -> None:
        """Answer without reading the request body: the connection is closed after it, so an
        unread body is never taken for the next request."""
        self.close_connection = True
        self._send(response, method)

    def _dispatch(self, method: str) -> None:
        ui = self.webui
        started = time.time()
        try:
            if not ui.host_ok(self.headers.get("Host", "")):
                return self._refuse(Response(421, b"Wrong host", "text/plain"), method)
            url = urlparse(self.path)
            path = url.path or "/"
            query = parse_qs(url.query, keep_blank_values=True)
            body = b""
            if method == "POST":
                if not ui.cookie_ok(self.headers.get("Cookie", "")):
                    return self._refuse(_login_required(), method)
                if not ui.origin_ok(self.headers.get("Origin", "")):
                    return self._refuse(Response(403, b"Cross-site request refused", "text/plain"), method)
                if self.headers.get("Transfer-Encoding"):
                    return self._refuse(Response(411, b"Content-Length required", "text/plain"), method)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return self._refuse(Response(400, b"Bad Content-Length", "text/plain"), method)
                limit = MAX_UPLOAD if path.endswith(UPLOAD_PATHS) else MAX_BODY
                if length < 0 or length > limit:
                    return self._refuse(Response(413, b"Request too large", "text/plain"), method)
                body = self.rfile.read(length) if length > 0 else b""
            if path == "/auth":
                return self._auth(query, method)
            if path.startswith("/static/"):
                return self._send(_static(path[len("/static/"):]), method)
            if not ui.cookie_ok(self.headers.get("Cookie", "")):
                return self._send(_login_required(), method)
            from core.database.connection import pages_wait
            wait = pages_wait()
            if wait:                                # (v5.44.5) the base is being checked and updated: no page yet
                return self._send(_waiting_page(wait, api=path.startswith("/api/")), method)
            handler, params = ui.router.match("GET" if method == "HEAD" else method, path)
            if handler is None:
                if params == "method":
                    return self._send(Response(405, b"Method not allowed", "text/plain"), method)
                return self._send(Response.not_found(), method)
            request = Request(method, path, params or {}, query, dict(self.headers.items()), body, ui.settings(), ui)
            response = handler(request)
            if response.stream is not None:
                started = None                      # an endless answer: not a slow page
        except (ConnectionError, TimeoutError):    # the browser went away or stopped reading
            self.close_connection = True
            return
        except Exception as exc:  # one broken page must not take the server down; show what happened
            _LOG.error("web page %s failed: %s\n%s", _loggable(self.path), exc, traceback.format_exc())
            self.close_connection = True
            response = Response(500, _error_page(exc).encode("utf-8"))
        try:
            self._send(response, method)
        except (ConnectionError, TimeoutError):
            self.close_connection = True
            return
        finally:
            took = time.time() - started if started is not None else 0.0
            if took > 1.0:
                _LOG.info("slow page %.1fs: %s", took, _loggable(self.path))

    def _auth(self, query: dict, method: str) -> None:
        token = (query.get("t") or [""])[0]
        if not secrets.compare_digest(str(token), self.webui.token):
            return self._send(_login_required(), method)
        response = Response.redirect(_local_target((query.get("next") or ["/posts"])[0]))
        response.headers["Set-Cookie"] = f"{COOKIE}={self.webui.token}; Path=/; HttpOnly; SameSite=Strict"
        self._send(response, method)

    def _send(self, response: Response, method: str) -> None:
        if response.file is not None:
            return self._send_file(response, method)
        if response.stream is not None:
            return self._send_stream(response, method)
        body = response.body if method != "HEAD" else b""
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", response.headers.pop("Cache-Control", "no-store"))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if response.content_type.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for key, value in response.headers.items():
            self.send_header(key, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_stream(self, response: Response, method: str) -> None:
        """An answer of unknown length (the frames of a video played in the page): every part is sent as it comes;
        the connection ends with it.  The browser going away ends it too."""
        self.close_connection = True
        stream = response.stream
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            if method == "HEAD":
                return
            for chunk in stream:
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (ConnectionError, TimeoutError, OSError):   # the page went away
            pass
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()

    def _send_file(self, response: Response, method: str) -> None:
        path = Path(response.file)
        if FILE_STREAMS.held(path):
            return self._send(Response(503, "Файл сейчас перемещается".encode("utf-8"), "text/plain; charset=utf-8",
                                       {"Retry-After": "2"}), method)
        try:
            size = path.stat().st_size
        except OSError:
            return self._send(Response.not_found("Файл не найден"), method)
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip()) if rng else None
        if m and m.group(1) and m.group(2) and int(m.group(2)) < int(m.group(1)):
            m = None                    # "bytes=5-2" is not a range at all: the whole file
        if m and (m.group(1) or m.group(2)) and size:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            elif m.group(2):
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", response.content_type or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", response.headers.pop("Cache-Control", "private, max-age=3600"))
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        for key, value in response.headers.items():
            self.send_header(key, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if method == "HEAD" or not length:
            return
        left = length
        with FILE_STREAMS.streaming(path, self.connection):
            try:
                fh = open(path, "rb")
            except OSError as exc:          # moved away after the headers went out: end this connection
                _LOG.debug("file %s vanished while answering: %s", path, exc)
                self.close_connection = True
                return
            with fh:
                fh.seek(start)
                while left > 0:
                    chunk = fh.read(min(1024 * 512, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        if left > 0:                     # the file got shorter while it was sent: the length was wrong
            self.close_connection = True


def _local_target(value: str) -> str:
    """Where ``/auth`` may send the browser: a path on this server, nothing else.

    Browsers read ``/\\host``, ``/<tab>/host`` and the like as another site, and a line break would
    write a header of its own: such addresses go to the gallery instead.  Letters outside ASCII
    are percent-encoded (a header must be ASCII)."""
    value = str(value or "")
    if (not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        return "/posts"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/posts"
    return quote(value, safe="/?&=%:@!$'()*+,;-._~#")


def _loggable(text: str) -> str:
    """The request line for the log without the session token of ``/auth?t=…``."""
    return re.sub(r"([?&]t=)[^&\s]*", r"\1…", str(text))


def _static(name: str) -> Response:
    name = name.split("?", 1)[0]
    target = (STATIC_DIR / name).resolve()
    try:
        target.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return Response.not_found()
    if not target.is_file():
        return Response.not_found()
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in {"application/javascript"}:
        ctype += "; charset=utf-8"
    return Response(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})


def _login_required() -> Response:
    text = ("<!doctype html><meta charset=utf-8><title>Local Booru</title>"
            "<body style='font:15px sans-serif;padding:40px;background:#1c1e25;color:#d9dbe4'>"
            "<h2>Откройте галерею из программы Local Booru</h2>"
            "<p>Эта страница доступна только по ссылке, которую даёт сама программа.</p></body>")
    return Response(403, text.encode("utf-8"))


def _waiting_page(wait: dict, *, api: bool) -> Response:
    """(v5.44.5) The base is older than the program and is checked before its update (``pages_wait``): pages say so and
    ask again every few seconds (they open by themselves once it is done); an API — 503 with the same words."""
    from html import escape
    reason = str(wait.get("reason") or "")
    if api:
        return Response.json({"ok": False, "error": reason, "waiting": not wait.get("failed")}, 503)
    again = "" if wait.get("failed") else "<meta http-equiv='refresh' content='3'>"
    title = "Базу обновить не удалось" if wait.get("failed") else "Программа готовит базу"
    text = (f"<!doctype html><meta charset=utf-8>{again}<title>Local Booru</title>"
            "<body style='font:15px sans-serif;padding:40px;background:#1c1e25;color:#d9dbe4;max-width:70ch'>"
            f"<h2>{escape(title)}</h2><p>{escape(reason)}</p></body>")
    return Response(503, text.encode("utf-8"), headers={"Retry-After": "3"})


def _error_page(exc: Exception) -> str:
    from html import escape
    return ("<!doctype html><meta charset=utf-8><title>Ошибка</title>"
            "<body style='font:14px sans-serif;padding:32px;background:#1c1e25;color:#d9dbe4'>"
            f"<h2>Страница не открылась</h2><p>{escape(type(exc).__name__)}: {escape(str(exc))}</p>"
            "<p>Подробности записаны в журнал программы.</p></body>")


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])
