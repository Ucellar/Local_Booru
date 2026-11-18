"""Standalone worker source: also available when Local Booru is frozen into an executable."""

BLOCKER_SCRIPT = r'''
function blocked(url) {
    const host = new URL(url).hostname.toLowerCase().replace(/\.+$/, '');
    return host === 'ya.ru' || host.endsWith('.ya.ru') ||
        host.split('.').some(part => part.startsWith('yandex') || part === 'yastatic');
}
browser.webRequest.onBeforeRequest.addListener(
    details => ({cancel: blocked(details.url)}), {urls: ['<all_urls>']}, ['blocking']);
'''

WORKER = r'''
import contextlib
import json
import sys
import time
from urllib.parse import urlsplit

wire = sys.stdout
MAX_HTML = 8 * 1024 * 1024
MAX_BYTES = 32 * 1024 * 1024

def reply(value):
    wire.write(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")
    wire.flush()

def address(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid address")
    if disabled_url(value):
        raise ValueError("disabled host")
    return parsed

def disabled_url(url):
    # Standalone copy of core.network's rule; tested for parity, applies to subresources too.
    host = (urlsplit(url).hostname or "").strip(".").lower()
    return host == "ya.ru" or host.endswith(".ya.ru") or any(
        part.startswith("yandex") or part == "yastatic" for part in host.split("."))

def installed_binary():
    from camoufox.pkgman import INSTALL_DIR, launch_path
    try:
        from camoufox.multiversion import get_active_path
    except ImportError:  # older packages use a single install directory
        location = INSTALL_DIR
    else:
        location = get_active_path() or INSTALL_DIR
    # Never call launch_path() without a path: it otherwise fetches a missing browser.
    return launch_path(location)

def navigation_pending(exc):
    words = str(exc).lower()
    return any(marker in words for marker in (
        "execution context was destroyed", "cannot find context with specified id",
        "unable to retrieve content because the page is navigating"))

def check_network_rule(page):
    # This reserved URL is always intercepted locally, even if the addon failed to load.
    # The native blocker must cancel it before Playwright can serve the synthetic page.
    probe = "https://yandex.local-booru.invalid/"
    served = False
    def synthetic(route):
        nonlocal served
        served = True
        route.fulfill(status=200, content_type="text/html", body="<h1>local test</h1>")
    page.route(probe, synthetic)
    try:
        for _ in range(3):
            served = False
            try:
                page.goto(probe, wait_until="domcontentloaded", timeout=2000)
            except Exception as exc:
                words = str(exc).lower()
                if not served and not page.is_closed() and any(
                        marker in words for marker in ("ns_error_abort", "ns_error_failure", "blocked_by")):
                    return
            page.wait_for_timeout(100)
        raise RuntimeError("browser network rule unavailable")
    finally:
        page.unroute(probe)

def main():
    # Do not create any child until the parent has attached us to its process-lifetime job.
    if sys.stdin.readline().strip() != "start":
        return
    from camoufox.sync_api import Camoufox
    with Camoufox(executable_path=installed_binary(), headless=True, main_world_eval=True, addons=[sys.argv[1]],
                  firefox_user_prefs={"media.volume_scale": "0.0", "extensions.allowPrivateBrowsingByDefault": True}) as browser:
        context = browser.new_context(accept_downloads=False)
        page = context.new_page()
        check_network_rule(page)
        bytes_page = context.new_page()
        bytes_origin = ""
        page.set_default_timeout(15000)
        status = 0
        def on_response(response):
            nonlocal status
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                status = response.status
        page.on("response", on_response)
        page.on("dialog", lambda dialog: dialog.dismiss())
        reply({"ok": True, "ready": True})
        for line in sys.stdin:
            try:
                request = json.loads(line)
                action = request["action"]
                if action == "close":
                    return
                if action == "open":
                    address(request["url"])
                    status = 0
                    page.goto(request["url"], wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(350)
                if action in ("open", "snapshot"):
                    before = page.url
                    if page.evaluate("document.readyState") == "loading":
                        reply({"ok": True, "pending": True})
                        continue
                    content = page.content()
                    if len(content.encode("utf-8")) > MAX_HTML:
                        raise ValueError("page too large")
                    agent = page.evaluate("navigator.userAgent")
                    if before != page.url:
                        reply({"ok": True, "pending": True})
                        continue
                    reply({"ok": True, "html": content, "url": page.url, "status": status,
                           "agent": agent, "cookies": context.cookies([page.url])})
                elif action == "bytes":
                    target = address(request["url"])
                    referer = request.get("referer") or target.scheme + "://" + target.netloc + "/"
                    origin = address(referer)
                    target_origin = target.scheme + "://" + target.netloc
                    ref = referer if (origin.scheme, origin.netloc) == (target.scheme, target.netloc) else target_origin + "/"
                    if bytes_origin != target_origin:
                        # Equivalent to Qt's setHtml(baseUrl): a local empty document with the target origin.
                        # The actual fetch still goes through Firefox, its TLS and the shared site cookies.
                        blank = target_origin + "/.local-booru-fetch-context"
                        bytes_page.route(blank, lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
                        try:
                            bytes_page.goto(blank, wait_until="domcontentloaded", timeout=15000)
                        finally:
                            bytes_page.unroute(blank)
                        bytes_origin = target_origin
                    limit = min(MAX_BYTES, max(1, int(request.get("limit") or MAX_BYTES)))
                    result = bytes_page.evaluate(r"""mw:async ({url,limit,ref}) => {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), 45000);
                        try {
                            const response = await fetch(url, {credentials:'include', referrer:ref, cache:'no-store', signal:controller.signal});
                            const reader = response.body.getReader();
                            const chunks=[]; let total=0;
                            while (true) {
                                const {done,value}=await reader.read(); if(done) break;
                                total+=value.length;
                                if(total>limit) { await reader.cancel(); throw new Error('response too large'); }
                                chunks.push(value);
                            }
                            let binary='';
                            for (const chunk of chunks) for(let i=0;i<chunk.length;i+=16384)
                                binary+=String.fromCharCode(...chunk.subarray(i,i+16384));
                            return {status:response.status, type:response.headers.get('content-type')||'',
                                    data:btoa(binary)};
                        } finally { clearTimeout(timer); }
                    }""", {"url": request["url"], "limit": limit, "ref": ref})
                    reply({"ok": True, **result})
                elif action == "cookies":
                    host = address(request["origin"]).hostname.lower()
                    accepted = []
                    removed = 0
                    for cookie in request.get("cookies", []):
                        domain = str(cookie.get("domain") or host).lower().lstrip(".")
                        if not domain or not (host == domain or host.endswith("." + domain)):
                            continue
                        if not cookie.get("name"):
                            continue
                        clean = {"name": str(cookie["name"]), "value": str(cookie.get("value") or ""), "httpOnly": True,
                                 "domain": str(cookie.get("domain") or host), "path": str(cookie.get("path") or "/")}
                        expires = cookie.get("expires", cookie.get("expirationDate"))
                        expired = isinstance(expires, (int, float)) and 0 < expires <= time.time()
                        if request.get("forget") or expired:
                            context.clear_cookies(name=clean["name"], domain=clean["domain"], path=clean["path"])
                            removed += 1
                        else:
                            for key in ("secure", "httpOnly"):
                                if key in cookie:
                                    clean[key] = bool(cookie[key])
                            if cookie.get("sameSite") in ("Strict", "Lax", "None"):
                                clean["sameSite"] = cookie["sameSite"]
                            if isinstance(expires, (int, float)) and expires > 0:
                                clean["expires"] = expires
                            accepted.append(clean)
                    if accepted:
                        context.add_cookies(accepted)
                    reply({"ok": True, "count": len(accepted) + removed})
                elif action not in ("open", "snapshot", "bytes", "cookies"):
                    raise ValueError("unknown request")
            except Exception as exc:
                # Never echo URLs, cookies or exception contents across the diagnostic boundary.
                if request.get("action") in ("open", "snapshot") and not page.is_closed() and navigation_pending(exc):
                    reply({"ok": True, "pending": True})
                else:
                    reply({"ok": False, "error": type(exc).__name__})

try:
    with contextlib.redirect_stdout(sys.stderr):
        main()
except Exception as exc:
    reply({"ok": False, "startup_error": type(exc).__name__})
'''
