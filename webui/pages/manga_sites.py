"""(v5.44.78) «Настройки → Сайты манги»: the addresses of the sites of manga and manhwa (a site that moved) and the
owner's sign-in on them — cookies from a file, MangaLib's token (``core/manhwa_access.py``).  The page shows what there
is (how many cookies, of which domains, until when the token lasts) — never a value; the values come only in (a file
read by the page, a pasted token) and stay in the program's settings folder.  The script — ``static/manga-sites.js``.
(v5.44.100) The sign-in from the owner's browser — the extension «Local Booru Companion» 0.10.0 takes it by itself;
here: whether the extension is there, «Открыть сайт» (in the browser of the extension, else the usual one) and
«Обновить вход» (the extension sends the site's sign-in now) of each site.
"""
from __future__ import annotations

import time

from webui.html import esc
from webui.server import Request, Response


LANGUAGES = (("ru", "Русский"), ("en", "English"), ("original", "Оригинал — язык, на котором тайтл вышел"))


def register(router) -> None:
    router.add("GET", r"/api/manga-sites/state", state)
    router.add("POST", r"/api/manga-sites/language", language)
    router.add("POST", r"/api/manga-sites/addresses", addresses)
    router.add("POST", r"/api/manga-sites/cookies", cookies)
    router.add("POST", r"/api/manga-sites/token", token)
    router.add("POST", r"/api/manga-sites/forget", forget)
    router.add("POST", r"/api/manga-sites/open", open_site)          # (v5.44.100) the extension 0.10.0
    router.add("POST", r"/api/manga-sites/refresh", refresh_site)


def _row(name: str, label: str, own: str, mine: str, *, api: bool = False) -> str:
    key = f"{name}_api" if api else name
    title = f"{label} — API" if api else label
    note = "свой адрес API, если он отдельно" if api else "новый адрес, если сайт переехал"
    return (f'<div class="field-row{" ms-api" if api else ""}"><label for="ms-a-{esc(key)}">{esc(title)}</label>'
            f'<input type="text" id="ms-a-{esc(key)}" name="{esc(key)}" value="{esc(mine)}" placeholder="{esc(own)}" '
            f'autocomplete="off" spellcheck="false" inputmode="url"><small>{esc(note)}</small></div>')


def settings_block(settings: dict) -> str:
    from core import manhwa_access as A
    rows, signin = [], []
    from core import manhwa_sources as S
    now = S.LANGUAGE[0]                   # the language in place (``manhwa_access.apply`` — of the settings)
    options = "".join(f'<option value="{esc(v)}"{" selected" if v == now else ""}>{esc(t)}</option>'
                      for v, t in LANGUAGES)
    for site in A.sites():
        rows.append(_row(site["name"], site["label"], site["address"], site["mine"]))
        if site.get("api"):
            rows.append(_row(site["name"], site["label"], site["api"]["default"], site["api"]["mine"], api=True))
        token = ""
        name = site["name"]
        opens = S.by_name(name).site().rstrip("/") + "/" if S.by_name(name) else ""
        if site["token"]:
            token = (f'<div class="ms-token"><label for="ms-token-{esc(site["name"])}">Токен {esc(site["label"])}</label>'
                     f'<input type="password" id="ms-token-{esc(site["name"])}" data-ms-token autocomplete="off" '
                     f'spellcheck="false" placeholder="значение «auth» из хранилища сайта">'
                     f'<button type="button" data-action="ms-token">Сохранить токен</button></div>')
        elif site["name"] == "hentailib":         # (v5.44.96) one account of the Lib sites
            token = ('<p class="hint">Главы HentaiLib открывает вход MangaLib (вход у сайтов Lib общий) или его '
                     'собственный — его возьмёт расширение, когда вы откроете HentaiLib.</p>')
        elif site["name"] == "allhentai":         # (v5.44.99) its chapters — to a sign-in alone: the site's cookies
            token = ('<p class="hint">Главы AllHentai сайт показывает только тем, кто вошёл: войдите на сайте в своём '
                     'браузере — расширение передаст вход само (или загрузите cookies из файла).</p>')
        forget_token = ('<button type="button" data-action="ms-forget" data-what="token" hidden>Убрать токен</button>'
                        if site["token"] or name in A.BROWSER_TOKEN else "")
        signin.append(f"""<li class="ms-site" data-site="{esc(site["name"])}">
      <div class="ms-site-head"><b>{esc(site["label"])}</b> <span class="ms-state" data-ms-state>вход не задан</span></div>
      <div class="tool-actions">
        <a class="button" href="{esc(opens)}" target="_blank" rel="noopener noreferrer" data-action="ms-open">Открыть
          сайт</a>
        <button type="button" data-action="ms-refresh">Обновить вход</button>
        <label class="ms-file"><input type="file" accept=".txt,.json,text/plain,application/json" data-ms-file>
          <span class="ms-file-button button" aria-hidden="true">Загрузить cookies из файла…</span><span class="sr-only">
          Загрузить cookies сайта {esc(site["label"])} из файла</span></label>
        <button type="button" data-action="ms-forget" data-what="cookies" hidden>Убрать cookies</button>
        {forget_token}
        <span class="action-status" role="status"></span>
      </div>
      <p class="ms-missing" data-ms-missing hidden></p>{token}
    </li>""")
    return f"""
<section class="settings-block manga-sites" id="set-manga-sites">
  <h2>Сайты манги и манхвы</h2>
  <p class="hint">Откуда «Скачать → Манга / Манхва» и «Из архива» берут тайтлы. Здесь — новые адреса сайтов, которые
    переехали, и вход на сайты: тайтлы 18+ некоторые сайты показывают только тем, кто вошёл. Cookies и токен хранятся
    только в папке настроек программы и никуда не показываются — здесь видно лишь, есть ли они.</p>
  <form class="ms-language" data-ms-language>
    <h3>Язык глав MangaDex</h3>
    <p class="hint">На MangaDex у тайтла главы на разных языках. Программа берёт этот язык, если у тайтла он есть;
      на карточке тайтла можно выбрать другой («Язык глав»). Главы MangaDex для тайтлов других сайтов — только
      русские.</p>
    <div class="field-row"><label for="ms-language">Язык</label><select id="ms-language" name="language">{options}
      </select></div>
    <div class="tool-actions">
      <button type="submit">Сохранить язык</button>
      <span class="action-status" role="status"></span>
    </div>
  </form>
  <form class="ms-addresses" data-ms-addresses>
    <h3>Адреса сайтов</h3>
    <p class="hint">Пусто — свой адрес сайта (он в поле серым). Ссылки и старого, и нового адреса программа узнаёт.</p>
    {"".join(rows)}
    <div class="tool-actions">
      <button type="submit" class="primary">Сохранить адреса</button>
      <span class="action-status" role="status"></span>
    </div>
  </form>
  <div class="ms-signin">
    <h3>Вход на сайты</h3>
    <p class="ms-companion" data-ms-companion>{_companion_words(A.browser_state())}</p>
    <ul class="ms-sites">{"".join(signin)}</ul>
    <details class="ms-how"><summary>Как взять cookies и токен</summary>
      <p>Проще всего — расширение программы «Local Booru Companion» 0.10 в вашем браузере: откройте сайт кнопкой
        «Открыть сайт» (или как обычно) и войдите — его cookies, а у MangaLib и HentaiLib и токен, расширение передаст
        программе само. Вышли и вошли снова, сайт сменил cookies — передаст новые; «Обновить вход» — сразу. Только
        сайтам из этого списка; значения нигде не показываются. Сайт, переехавший на новый адрес, расширение увидит,
        когда вы разрешите его: значок расширения → «Ещё» → «Разрешить сайты манги».</p>
      <p>Без расширения — файлом. Cookies: откройте сайт в браузере, где вы на нём вошли, и сохраните cookies этого
        сайта расширением для cookies — например, «Get cookies.txt LOCALLY» (файл cookies.txt) или «Cookie-Editor»
        («Export» → JSON в файл). Потом «Загрузить cookies из файла…» у этого сайта. Из файла программа берёт только
        cookies этого сайта.</p>
      <p>Токен MangaLib: на mangalib.me, где вы вошли, нажмите F12 → «Application» (в Firefox — «Хранилище») → «Local
        Storage» → адрес сайта → строка «auth». Скопируйте её значение целиком (оно начинается с «{{») и вставьте в поле
        «Токен MangaLib». Токен живёт долго; когда закончится, программа скажет — возьмите новый так же. Он же
        открывает главы HentaiLib: вход у сайтов Lib общий.</p>
      <p>Платные главы вход не открывает: программа их не покупает и не скачивает.</p>
    </details>
  </div>
</section>"""


def _companion_words(state: dict) -> str:
    """(v5.44.100) Whether the extension takes the sign-in from the browser now."""
    if state.get("connected"):
        return ("Расширение «Local Booru Companion» подключено: вход на эти сайты оно берёт из вашего браузера само — "
                "откройте сайт и войдите.")
    if state.get("old"):
        return ("Расширение подключено, но старой версии — вход оно не передаёт. Обновите его: страница расширений "
                "браузера (chrome://extensions) → у «Local Booru Companion» кнопка «Обновить» (↻).")
    return ("Расширение «Local Booru Companion» сейчас не подключено (браузер закрыт или расширение не сопряжено с "
            "программой) — вход можно загрузить файлом. С расширением он берётся из браузера сам.")


def _signin_public() -> dict:
    from core import manhwa_access as A
    return A.summary()


def state(req: Request) -> Response:
    from core import manhwa_access as A
    from core import manhwa_sources as S
    browser = A.browser_state()
    return Response.json({"ok": True, "sites": A.sites(), "signin": _signin_public(), "language": S.LANGUAGE[0],
                          "now": int(time.time()), "browser": browser, "browser_words": _companion_words(browser),
                          "answers": A.browser_answers(), "asked": A.asked_answers()})


def language(req: Request) -> Response:
    """(v5.44.79) The language of MangaDex's chapters: "ru", "en", "original"."""
    from core import manhwa_access as A, manhwa_sources as S
    from webui import settings_store
    value = str(req.json().get("language") or "")
    if value not in S.LANGUAGES:
        return Response.json({"ok": False, "error": "Такого языка нет в списке"}, 400)
    if not settings_store.save({A.LANGUAGE_SETTING: value}):
        return Response.json({"ok": False, "error": "Программа не ответила — язык не сохранён"}, 503)
    S.use_language(value)
    return Response.json({"ok": True, "language": value})


def addresses(req: Request) -> Response:
    from core import manhwa_access as A, manhwa_sources as S
    from webui import settings_store
    body = req.json()
    asked = body.get("addresses") if isinstance(body.get("addresses"), dict) else {}
    keep, wrong = A.check_addresses(asked)
    if wrong:
        return Response.json({"ok": False, "error": "Не все адреса — адреса сайтов", "wrong": wrong}, 400)
    if not settings_store.save({A.SETTING: keep}):
        return Response.json({"ok": False, "error": "Программа не ответила — адреса не сохранены"}, 503)
    S.use_addresses(keep)
    return Response.json({"ok": True, "sites": A.sites()})


def _site(body: dict) -> str:
    return str(body.get("site") or "")[:40]


def cookies(req: Request) -> Response:
    from core import manhwa_access as A
    body = req.json()
    try:
        got = A.put_cookies(_site(body), str(body.get("text") or ""))
    except ValueError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    return Response.json({"ok": True, **got, "signin": _signin_public()})


def token(req: Request) -> Response:
    from core import manhwa_access as A
    body = req.json()
    try:
        got = A.put_token(_site(body), str(body.get("text") or ""))
    except ValueError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    return Response.json({"ok": True, **got, "signin": _signin_public()})


def open_site(req: Request) -> Response:
    """(v5.44.100) «Открыть сайт»: the extension opens it in its browser; {"queued": False} — no extension: the page
    opens the address the usual way."""
    from core import manhwa_access as A
    try:
        got = A.ask_browser(_site(req.json()), "open")
    except ValueError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    return Response.json({"ok": True, **got})


def refresh_site(req: Request) -> Response:
    """(v5.44.100) «Обновить вход»: the extension sends the site's sign-in now (the page waits for its answer —
    ``state`` ``answers``)."""
    from core import manhwa_access as A
    try:
        got = A.ask_browser(_site(req.json()), "refresh")
    except ValueError as exc:
        return Response.json({"ok": False, "error": str(exc)}, 400)
    if not got["queued"]:
        state = A.browser_state()
        return Response.json({"ok": False, "error": _companion_words(state)}, 409)
    return Response.json({"ok": True, **got})


def forget(req: Request) -> Response:
    from core import manhwa_access as A, manhwa_sources as S
    body = req.json()
    name, what = _site(body), str(body.get("what") or "all")
    if S.by_name(name) is None or what not in ("cookies", "token", "all"):
        return Response.json({"ok": False, "error": "Такого сайта программа не знает"}, 400)
    A.forget(name, what)
    return Response.json({"ok": True, "signin": _signin_public()})
