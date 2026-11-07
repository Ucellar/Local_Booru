"""(v5.44.71) «Телефон» through the owner's own server — the program's side of it (``core/phone_relay.py``).

* «Настройки → Телефон» → «Через ваш сервер» (:func:`settings_part`; its script — ``static/phone-relay.js``): the
  server's address, login and password — «Подключить сервер» (a page task: ``phone_relay_setup.setup``; the password
  goes to it and nowhere else, the task keeps it only while it runs), the state of the connection, «Подключить
  телефон» — the QR code of ``/m/pair?c=…`` and the eight digits (``core/phone_devices``; a code is made when the page
  asks and none works), the phones paired with «Отключить», «Проверить» (the phone's whole way from this computer),
  «Выключить» / «Включить», «Настроить заново», «Убрать с сервера» (the password once more), «Забыть сервер»;
* the phone's side: ``/m/pair`` — the QR code's address and the page for the eight digits; a phone not paired yet gets
  only that (:func:`pair_needed`); ``/m/alive`` — the server's check of the way (every 10 s) and the setup's own
  (:func:`alive`, answered before the host is looked at: the server's check comes to its own port's name).
"""
from __future__ import annotations

import logging
import time
from urllib.parse import parse_qs

from webui.html import esc
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.phone")
TASK = "phone_relay"


def register(router) -> None:
    router.add("GET", r"/m/pair", pair_page)
    router.add("POST", r"/m/pair", pair_post)
    router.add("GET", r"/api/phone/relay/state", state)
    router.add("POST", r"/api/phone/relay/setup", setup)
    router.add("POST", r"/api/phone/relay/remove", remove)
    router.add("GET", r"/api/phone/relay/task", task_state)
    router.add("POST", r"/api/phone/relay/switch", switch)
    router.add("POST", r"/api/phone/relay/forget", forget)
    router.add("POST", r"/api/phone/relay/check", check)
    router.add("POST", r"/api/phone/pair/new", pair_new)
    router.add("GET", r"/api/phone/pair\.svg", pair_qr)
    router.add("POST", r"/api/phone/devices/remove", device_remove)


# ── «Настройки → Телефон» ───────────────────────────────────────────────────────────────────────────────────────
def settings_part(settings: dict) -> str:
    host = str(settings.get("phone_relay_host") or "")
    port = int(settings.get("phone_relay_ssh_port") or 22)
    return f"""
  <div class="phone-way phone-relay" id="phone-relay" data-relay-configured="{'1' if settings.get('phone_relay_address') else ''}">
    <h3>Через ваш сервер</h3>
    <p class="hint">Программа сама держит защищённый канал от этого компьютера к вашему серверу (VPS), а на сервере рядом
      с VPN ставит маленький веб-вход с сертификатом https. На телефон ничего ставить не нужно: адрес открывается в
      браузере — с VPN и без. Войти можно только с телефона, который вы подключите кодом с этого экрана.</p>
    <div class="phone-relay-status" data-relay-status aria-live="polite"><p class="hint">Смотрю…</p></div>
    <form class="phone-relay-form" data-relay-form autocomplete="off" hidden>
      <div class="field-row" data-relay-host-row><label for="relay-host">Адрес сервера</label>
        <input id="relay-host" name="host" type="text" inputmode="url" spellcheck="false" autocomplete="off"
          placeholder="203.0.113.5" value="{esc(host)}"></div>
      <div class="field-row" data-relay-host-row><label for="relay-port">Порт SSH</label>
        <input id="relay-port" name="port" type="number" min="1" max="65535" value="{port}"></div>
      <div class="field-row"><label for="relay-login">Логин</label>
        <input id="relay-login" name="login" type="text" spellcheck="false" autocomplete="off" value="root"></div>
      <div class="field-row"><label for="relay-password">Пароль</label>
        <input id="relay-password" name="password" type="password" autocomplete="new-password"></div>
      <p class="hint" data-relay-form-hint>Адрес, логин и пароль — те же, что вы вводили в Amnezia. Пароль нужен один раз:
        программа входит с ним, заводит на сервере отдельный вход только для этого канала (по ключу) и пароль нигде не
        сохраняет. VPN и всё остальное на сервере не трогается.</p>
      <div class="tool-actions"><button type="submit" class="primary" data-relay-go>Подключить сервер</button>
        <button type="button" data-action="relay-cancel" hidden>Отмена</button>
        <span class="action-status" role="status"></span></div>
    </form>
    <div class="phone-relay-run" data-relay-run hidden>
      <pre class="job-log" tabindex="0" aria-label="Как идёт настройка сервера"></pre>
    </div>
    <div class="phone-relay-on" data-relay-on-box hidden>
      <div class="phone-pair" data-relay-pair>
        <img class="phone-qr" data-relay-qr alt="QR-код: подключить телефон" width="198" height="198">
        <div class="phone-pair-text">
          <p><b>Подключить телефон:</b> наведите на код камеру телефона.</p>
          <p>Или откройте на телефоне <a data-relay-address target="_blank" rel="noopener"></a> и введите код
            <b class="phone-pair-code" data-relay-code></b></p>
          <p class="hint" data-relay-until></p>
          <p class="hint">Один код — один телефон. Потом на телефоне: меню браузера → «Добавить на главный экран».</p>
          <div class="tool-actions"><button type="button" data-action="relay-new-code">Новый код</button></div>
        </div>
      </div>
      <h4 class="phone-devices-title">Подключённые телефоны</h4>
      <ul class="phone-devices" data-relay-devices></ul>
      <div class="tool-actions">
        <button type="button" data-action="relay-check">Проверить</button>
        <button type="button" data-action="relay-switch" data-on="">Выключить</button>
        <button type="button" data-action="relay-switch" data-on="1" hidden>Включить</button>
        <button type="button" data-action="relay-redo">Настроить заново</button>
        <button type="button" data-action="relay-remove">Убрать с сервера…</button>
        <button type="button" data-action="relay-forget">Забыть сервер</button>
        <span class="action-status" role="status"></span>
      </div>
    </div>
  </div>"""


def _code_public(cfg) -> dict:
    from core import phone_devices
    code = phone_devices.code() or phone_devices.new_code()
    return {"short": f"{code['short'][:4]} {code['short'][4:]}", "until": int(code["until"])}


def state(req: Request) -> Response:
    from core import page_jobs, phone_devices, phone_relay as R
    s = req.settings
    cfg = R.config_of(s)
    task = page_jobs.current(TASK)
    out = {"ok": True, "available": R.available(), "on": bool(s.get("phone_relay")),
           "configured": bool(s.get("phone_relay_address")), "host": str(s.get("phone_relay_host") or ""),
           "ssh_port": int(s.get("phone_relay_ssh_port") or 22), "address": str(s.get("phone_relay_address") or ""),
           "link": R.state(s), "devices": phone_devices.listing(),
           "task": {"id": task.id, "status": task.status} if task is not None else None}
    if cfg is not None and req.arg("code") == "1":
        out["code"] = _code_public(cfg)
    return Response.json(out)


def _secret_task(label: str, work, req: Request) -> Response:
    """A page task that gets the password and lets it go when it is done (the task object stays as the last one)."""
    from core import page_jobs
    body = req.json()
    secret = {"password": str(body.get("password") or "")}
    if not secret["password"]:
        return Response.json({"ok": False, "error": "Введите пароль сервера"}, 400)
    host, ssh_port, login = str(body.get("host") or ""), body.get("port") or 22, str(body.get("login") or "root")
    server_port = req.server.port if req.server is not None else 0
    settings = dict(req.settings)

    def run(say, stop_check):
        password = secret.pop("password", "")
        return work(host, ssh_port, login, password, server_port, settings, say, stop_check)
    try:
        task = page_jobs.start(TASK, label, run)
    except page_jobs.TaskBusy as exc:
        secret.clear()
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def setup(req: Request) -> Response:
    """«Подключить сервер»: {host, port, login, password}."""
    def work(host, ssh_port, login, password, server_port, settings, say, stop_check):
        from core import phone_relay as R, phone_relay_setup as setup_
        from core.settings import load_settings
        from webui import settings_store
        R.SETUP_GOING.set()
        try:
            result = setup_.setup(host, ssh_port, login, password, port=server_port, settings=settings, say=say,
                                  stop=stop_check)
            if result.get("changes"):
                if not settings_store.save(result["changes"]):
                    result = {"failed": "Окно программы не ответило — настройка не сохранилась"}
                elif result.get("warn"):
                    say("Осталось: " + result["warn"])
                else:
                    say(f"Готово. Адрес для телефона: {result['address']} — подключите телефон кодом")
            return result
        finally:
            R.SETUP_GOING.clear()
            R.keep(load_settings(), server_port)        # what is saved runs (a failed setup — as it was before)
    return _secret_task("Связь с телефоном через сервер", work, req)


def remove(req: Request) -> Response:
    """«Убрать с сервера»: {login, password} (the server — the one set up)."""
    def work(host, ssh_port, login, password, server_port, settings, say, stop_check):
        from core import phone_relay_setup as setup_
        from webui import settings_store
        result = setup_.remove(host or settings.get("phone_relay_host") or "", ssh_port, login, password,
                               settings=settings, say=say, stop=stop_check)
        if result.get("changes") and not settings_store.save(result["changes"]):
            return {"failed": "С сервера убрано, но окно программы не ответило — нажмите «Забыть сервер»"}
        return result
    return _secret_task("Убрать с сервера", work, req)


def task_state(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(TASK)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    out = {"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))}
    result = task.result if isinstance(task.result, dict) else {}
    out["result"] = {k: result.get(k) for k in ("address", "warn", "failed") if result.get(k)}
    return Response.json(out)


def switch(req: Request) -> Response:
    """{on} — «Включить» / «Выключить» (the server stays set up)."""
    from core import phone_relay as R
    from webui import settings_store
    on = bool(req.json().get("on"))
    if on and not (req.settings.get("phone_relay_address") and req.settings.get("phone_relay_host_key")):
        return Response.json({"ok": False, "error": "Сервер ещё не настроен"}, 409)
    if not settings_store.save({"phone_relay": on}):
        return Response.json({"ok": False, "error": "Окно программы не ответило — не сохранено"}, 503)
    if on:
        R.keep(dict(req.settings, phone_relay=True), req.server.port if req.server is not None else 0)
    else:
        R.stop()
    return Response.json({"ok": True, "message": "Связь через сервер включена" if on else
                          "Связь через сервер выключена: телефон не войдёт, пока не включите"})


def forget(req: Request) -> Response:
    """«Забыть сервер»: the settings of the server and this computer's key away (on the server nothing changes)."""
    from core import phone_relay as R
    from webui import settings_store
    R.stop()
    if not settings_store.save({"phone_relay": False, "phone_relay_host": "", "phone_relay_address": "",
                                "phone_relay_host_key": ""}):
        return Response.json({"ok": False, "error": "Окно программы не ответило — не сохранено"}, 503)
    R.drop_key()
    return Response.json({"ok": True, "message": "Сервер забыт — его можно настроить заново"})


CHECK_WORDS = {"ok": "Связь есть: телефон откроет {address}",
               "cert": "Сертификат https сервера пока не готов — подождите несколько минут",
               "connect": "Адрес {address} не отвечает этому компьютеру: порт закрыт брандмауэром хостинга или сервер "
                          "выключен",
               "other": "На адресе {address} отвечает не программа"}


def check(req: Request) -> Response:
    """«Проверить»: the phone's whole way from this computer (the internet → the server → the connection → here)."""
    from core import phone_relay as R, phone_relay_setup as setup_
    cfg = R.config_of(req.settings)
    if cfg is None:
        return Response.json({"ok": False, "error": "Связь через сервер выключена"}, 409)
    link = R.state(req.settings)
    if link["state"] != "up":
        return Response.json({"ok": False, "error": link.get("why") or "Канал «компьютер — сервер» ещё не готов"})
    result = setup_.probe(cfg.address)
    words = CHECK_WORDS.get(result, CHECK_WORDS["other"]).format(address=cfg.address)
    return Response.json({"ok": result == "ok", "message": words, **({} if result == "ok" else {"error": words})})


def pair_new(req: Request) -> Response:
    from core import phone_devices, phone_relay as R
    cfg = R.config_of(req.settings)
    if cfg is None:
        return Response.json({"ok": False, "error": "Связь через сервер выключена"}, 409)
    phone_devices.new_code()
    return Response.json({"ok": True, "code": _code_public(cfg)})


def pair_qr(req: Request) -> Response:
    """The QR code of the pairing address (the code that works now; made when none)."""
    from core import phone_devices, phone_relay as R
    from webui.pages.phone import qr_svg
    cfg = R.config_of(req.settings)
    if cfg is None:
        return Response.not_found("Связь через сервер выключена")
    code = phone_devices.code() or phone_devices.new_code()
    drawing = qr_svg(f"{cfg.address}/m/pair?c={code['long']}")
    if not drawing:
        return Response.not_found("Кода нет")
    return Response(200, drawing.encode("utf-8"), "image/svg+xml", {"Cache-Control": "no-store"})


def device_remove(req: Request) -> Response:
    """{id} — «Отключить» a phone; {all: true} — all of them."""
    from core import phone_devices
    body = req.json()
    device = "" if body.get("all") else str(body.get("id") or "")
    if not device and not body.get("all"):
        return Response.json({"ok": False, "error": "Какой телефон?"}, 400)
    gone = phone_devices.remove(device)
    return Response.json({"ok": True, "removed": gone})


# ── the phone's side ────────────────────────────────────────────────────────────────────────────────────────────
def _paired(req: Request, token: str, form: bool) -> Response:
    from core import phone_devices
    if form:
        response = Response.redirect("/m/")
    else:
        response = Response.json({"ok": True, "go": "/m/"})
    secure = _header(req, "X-Forwarded-Proto").strip().lower() == "https"     # the server's Caddy says it
    response.headers["Set-Cookie"] = phone_devices.set_cookie(token, secure=secure)
    return response


def pair_page(req: Request) -> Response:
    """The QR code's address (``?c=``) — the phone is paired and goes in; else the page for the eight digits."""
    from core import phone_access, phone_devices
    if phone_access.admit(req.settings, req.headers, req.peer) == "ok":
        return Response.redirect("/m/")
    long = req.arg("c").strip()
    if long:
        if phone_devices.take(long=long):
            token = phone_devices.pair(_header(req, "User-Agent"))
            return _paired(req, token, form=True)
        return _pair_form("Этот код уже использован или устарел — на компьютере уже другой: наведите камеру ещё раз "
                          "или введите восемь цифр", 410)
    return _pair_form()


def pair_post(req: Request) -> Response:
    from core import phone_devices
    body = req.json()
    form = not body
    if form:
        body = {k: v[0] for k, v in parse_qs(req.body.decode("utf-8", "replace")).items() if v}
    short = str(body.get("code") or "")[:20]
    if phone_devices.take(short=short):
        token = phone_devices.pair(_header(req, "User-Agent"))
        return _paired(req, token, form=form)
    time.sleep(1.0)                                   # a guess costs a second
    words = "Код не подошёл — проверьте цифры на экране компьютера (код действует 15 минут)"
    return _pair_form(words, 403) if form else Response.json({"ok": False, "error": words}, 403)


def _header(req: Request, name: str) -> str:
    low = name.lower()
    return str(next((v for k, v in (req.headers or {}).items() if str(k).lower() == low), "") or "")


def _pair_form(error: str = "", status: int = 200) -> Response:
    from webui.pages.phone import phone_frame, NO_STORE_OFF
    body = f"""
<section class="m-hello m-pair">
  <div class="m-welcome-mark"><span class="m-brand" aria-hidden="true">LB</span></div>
  <h2>Подключите телефон</h2>
  <p>Это вход в вашу библиотеку Local Booru. Открыть её можно только с телефона, подключённого на компьютере.</p>
  <ol class="m-steps"><li>На компьютере: «Настройки → Телефон».</li>
    <li>Наведите камеру телефона на код — или введите восемь цифр под ним.</li></ol>
  <form method="post" action="/m/pair" class="m-pair-form">
    <label class="m-field"><span class="m-sr">Код с компьютера</span>
      <input name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="9"
        pattern="[0-9 ]*" placeholder="1234 5678" required></label>
    <button type="submit" class="m-button">Подключить</button>
  </form>
  <p class="m-pair-error" role="alert"{'' if error else ' hidden'}>{esc(error)}</p>
</section>"""
    # the form's POST carries the page's origin only with a referrer policy that lets it (the server says «no-referrer»
    # for every page — a browser then sends «Origin: null», and the server refuses it as another site's)
    page = phone_frame("Подключите телефон", body, tabs=False, heading="Local Booru",
                       head='\n<meta name="referrer" content="same-origin">')
    return Response(status, page.encode("utf-8"), headers=dict(NO_STORE_OFF))


def pair_needed(path: str, method: str, headers) -> Response:
    """What a phone not paired yet gets for any other address: a page — the pairing page; the rest — 401."""
    from core.phone_access import _header as header
    navigate = (method in ("GET", "HEAD") and not path.startswith("/api/")
                and (header(headers, "Sec-Fetch-Mode").lower() == "navigate" or "text/html" in header(headers, "Accept")))
    if navigate:
        return Response.redirect("/m/pair")
    return Response.json({"ok": False, "error": "Телефон не подключён: откройте адрес библиотеки и введите код с "
                                                "компьютера"}, 401)


def alive(query: str) -> Response:
    """``/m/alive``: the server's check of the way — «ok»; the setup's own (``?n=`` — its word) — and «X-LB-Relay»."""
    from core import phone_relay as R
    nonce = (parse_qs(query or "").get("n") or [""])[0]
    headers = {"Cache-Control": "no-store"}
    if R.probe_answer(nonce):
        headers["X-LB-Relay"] = "1"
    return Response(200, b"ok", "text/plain; charset=utf-8", headers)
