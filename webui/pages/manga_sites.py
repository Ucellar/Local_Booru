"""(v5.44.78) «Настройки → Манга и манхва»: the addresses of the sites of manga and manhwa (a site that moved) and the
owner's sign-in on them — cookies from a file, MangaLib's token (``core/manhwa_access.py``).  The page shows what there
is (how many cookies, of which domains, until when the token lasts) — never a value; the values come only in (a file
read by the page, a pasted token) and stay in the program's settings folder.  The script — ``static/manga-sites.js``.
(v5.44.100) The sign-in from the owner's browser — the extension «Local Booru Companion» 0.10.0 takes it by itself;
here: whether the extension is there, «Открыть сайт» (in the browser of the extension, else the usual one) and
«Обновить вход» (the extension sends the site's sign-in now) of each site.
"""
from __future__ import annotations

import json
import time

from webui.html import esc
from webui.server import Request, Response


LANGUAGES = (("ru", "Русский"), ("en", "English"), ("original", "Оригинал — язык, на котором тайтл вышел"))
LANGUAGE_WORDS = {"ru": "русский", "en": "английский", "original": "язык оригинала"}   # «главы: …» in a site's row
LANGUAGE_SITE = "mangadex"            # the site whose chapters come in many languages: the choice is in its row


def register(router) -> None:
    router.add("GET", r"/api/manga-sites/state", state)
    router.add("POST", r"/api/manga-sites/save", save_all)           # (v5.44.161) the page's one «Сохранить»
    router.add("POST", r"/api/manga-sites/language", language)
    router.add("POST", r"/api/manga-sites/addresses", addresses)
    router.add("POST", r"/api/manga-sites/cookies", cookies)
    router.add("POST", r"/api/manga-sites/token", token)
    router.add("POST", r"/api/manga-sites/forget", forget)
    router.add("POST", r"/api/manga-sites/open", open_site)          # (v5.44.100) the extension 0.10.0
    router.add("POST", r"/api/manga-sites/refresh", refresh_site)


def _row(name: str, label: str, own: str, mine: str, *, api: bool = False) -> str:
    key = f"{name}_api" if api else name
    title = "Адрес API" if api else "Адрес сайта"
    note = "свой адрес API, если он отдельно" if api else "новый адрес, если сайт переехал; пусто — свой адрес сайта"
    return (f'<label class="ms-l" for="ms-a-{esc(key)}">{title}</label>'
            f'<div class="ms-field{" ms-api" if api else ""}">'
            f'<input type="text" id="ms-a-{esc(key)}" name="{esc(key)}" value="{esc(mine)}" placeholder="{esc(own)}" '
            f'autocomplete="off" spellcheck="false" inputmode="url" aria-label="{esc(label)} — {title.lower()}">'
            f'<small>{esc(note)}</small></div>')


# (v5.44.161) whose chapters need a sign-in: «must» — every chapter, «some» — the titles 18+ or a part of the chapters
NEED = {"allhentai": "must", "mangalib": "some", "hentailib": "some", "nudemoon": "some"}


def _host(address: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(address if "//" in address else "//" + address).netloc or address


def settings_block(settings: dict) -> str:
    """(v5.44.161, the mockup «Настройки → Манга и манхва») The sites as one table: the site, its sign-in in a word,
    its address; «настроить» opens the site's row — the sign-in, the token, the addresses (MangaDex: the language of
    its chapters).  The addresses and the language are saved by the page's one «Сохранить» (the form says where:
    ``data-save`` — :func:`save_all`); the sign-in — at once, by its own buttons.  The script fills the words of the
    sign-in (``static/manga-sites.js``): a value is never on the page."""
    from core import manhwa_access as A
    from core import manhwa_sources as S
    now = S.LANGUAGE[0]                   # the language in place (``manhwa_access.apply`` — of the settings)
    options = "".join(f'<option value="{esc(v)}"{" selected" if v == now else ""}>{esc(t)}</option>'
                      for v, t in LANGUAGES)
    items = []
    for site in A.sites():
        name = site["name"]
        fields = _row(name, site["label"], site["address"], site["mine"])
        if site.get("api"):
            fields += _row(name, site["label"], site["api"]["default"], site["api"]["mine"], api=True)
        token = ""
        opens = S.by_name(name).site().rstrip("/") + "/" if S.by_name(name) else ""
        if site["token"]:
            token = (f'<label class="ms-l" for="ms-token-{esc(name)}">Токен</label><div class="ms-token ms-field">'
                     f'<input type="password" id="ms-token-{esc(name)}" data-ms-token autocomplete="off" '
                     f'spellcheck="false" placeholder="значение «auth» из хранилища сайта" '
                     f'aria-label="Токен {esc(site["label"])}">'
                     f'<button type="button" data-action="ms-token">Сохранить токен</button></div>')
        elif name == "hentailib":         # (v5.44.96) one account of the Lib sites
            token = ('<p class="hint ms-note">Главы HentaiLib открывает вход MangaLib (вход у сайтов Lib общий) или его '
                     'собственный — его возьмёт расширение, когда вы откроете HentaiLib.</p>')
        elif name == "allhentai":         # (v5.44.99) its chapters — to a sign-in alone: the site's cookies
            token = ('<p class="hint ms-note">Главы AllHentai сайт показывает только тем, кто вошёл: войдите на сайте в своём '
                     'браузере — расширение передаст вход само (или загрузите cookies из файла).</p>')
        elif name == "nudemoon":          # (v5.44.131) behind Cloudflare's check for browsers
            token = ('<p class="hint ms-note">Nude-Moon сначала проверяет, что страницу открывает браузер (Cloudflare), и '
                     'пускает только сам браузер: его страницы и картинки берёт браузер окна программы — окно '
                     'программы должно быть открыто. Часть глав сайт показывает только тем, кто вошёл: войдите на '
                     'сайте в своём браузере — расширение передаст вход, и браузер окна будет заходить с ним.</p>')
        chapters = said = ""
        if name == LANGUAGE_SITE:         # (v5.44.79) the language of its chapters; (v5.44.161) in its own row
            chapters = ('<label class="ms-l" for="ms-language">Язык глав</label><div class="ms-field">'
                        f'<select id="ms-language" name="language" aria-label="Язык глав MangaDex">{options}</select>'
                        '<small>программа берёт этот язык, если у тайтла он есть; на карточке тайтла можно выбрать '
                        'другой («Язык глав»). Главы MangaDex для тайтлов других сайтов — только русские</small></div>')
            said = f'<span class="ms-lang" data-ms-lang> · главы: {esc(LANGUAGE_WORDS.get(now, now))}</span>'
        forget_token = ('<button type="button" data-action="ms-forget" data-what="token" hidden>Убрать токен</button>'
                        if site["token"] or name in A.BROWSER_TOKEN else "")
        address = site["mine"] or site["address"]
        # what the search of a setting finds the site by: its name and addresses («язык» — the site that has one)
        hosts = " ".join(dict.fromkeys([_host(address), *site["hosts"]])) + (" язык глав" if chapters else "")
        items.append(f"""<li class="ms-site" data-site="{esc(name)}" data-need="{NEED.get(name, "")}" data-find="{esc(hosts)}">
      <details class="ms-more">
        <summary><b class="ms-name">{esc(site["label"])}</b><span class="ms-state sx-chip" data-ms-state>…</span>
          <span class="ms-addr"><span data-ms-host>{esc(_host(address))}</span>{said}</span><span class="ms-toggle"><span class="ms-t-set">настроить</span><span class="ms-t-in">войти</span><span class="ms-t-up">свернуть</span></span></summary>
        <div class="ms-inner">
          <span class="ms-l">Вход</span>
          <div class="ms-field">
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
            <small class="ms-detail" data-ms-detail>Вход не задан.</small>
            <p class="ms-missing" data-ms-missing hidden></p>
          </div>
          {token}
          {fields}
          {chapters}
        </div>
      </details>
    </li>""")
    words = json.dumps(LANGUAGE_WORDS, ensure_ascii=False)
    return f"""
<section class="settings-block sx-block manga-sites" id="set-manga-sites" data-title="Сайты" data-ms-languages="{esc(words)}">
  <h3 class="sx-title">Сайты <small>вход нужен только там, где главы показывают вошедшим</small></h3>
  <form class="settings-form ms-addresses" data-form="manga-sites" data-save="/api/manga-sites/save" data-ms-addresses>
    <div class="sx-group ms-table">
      <div class="ms-head" aria-hidden="true"><span>Сайт</span><span>Вход</span><span>Адрес</span><span></span></div>
      <ul class="ms-sites">{"".join(items)}</ul>
    </div>
    <button type="submit" class="form-submit" hidden tabindex="-1">Сохранить</button>
    <p class="form-error" role="alert" hidden></p>
  </form>
  <p class="sx-note ms-companion" data-ms-companion>{_companion_words(A.browser_state())}</p>
  <p class="sx-note">Откуда «Скачать → Манга / Манхва» и «Из архива» берут тайтлы. Cookies и токен хранятся только в
    папке настроек программы и нигде не показываются — видно лишь, есть ли они. Ссылки и старого, и нового адреса
    сайта программа узнаёт.</p>
  <details class="ms-how sx-box"><summary>Как взять cookies и токен</summary>
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
        «Токен». Токен живёт долго; когда закончится, программа скажет — возьмите новый так же. Он же
        открывает главы HentaiLib: вход у сайтов Lib общий.</p>
      <p>Платные главы вход не открывает: программа их не покупает и не скачивает.</p>
  </details>
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


def save_all(req: Request) -> Response:
    """(v5.44.161) What the page's one «Сохранить» sends of «Сайты»: {site: address, site_api: address, "language"}.
    Checked whole before anything is kept — an address that is not one is named (``field`` — the first of them,
    ``wrong`` — each with its words) and nothing is saved; kept in one write.  ``values`` — what the fields hold now
    (an address as the program keeps it: «site.org» becomes «https://site.org»)."""
    from core import manhwa_access as A, manhwa_sources as S
    from webui import settings_store
    body = req.json()
    asked = {str(k): v for k, v in body.items() if k != "language" and isinstance(v, str)}
    keep, wrong = A.check_addresses(asked)
    if wrong:
        labels = {s["name"]: s["label"] for s in A.sites()}
        first = next(iter(wrong))
        site = labels.get(first.removesuffix("_api"), first)
        return Response.json({"ok": False, "error": f"«{site}»: это не адрес сайта — нужно вроде https://site.org, "
                              "без пути после него", "field": first, "wrong": wrong}, 400)
    changes = {A.SETTING: keep}
    if "language" in body:
        value = str(body.get("language") or "")
        if value not in S.LANGUAGES:
            return Response.json({"ok": False, "error": "Такого языка глав нет в списке", "field": "language"}, 400)
        changes[A.LANGUAGE_SETTING] = value
    if not settings_store.save(changes):
        return Response.json({"ok": False, "error": "Программа не ответила — не сохранено"}, 503)
    S.use_addresses(keep)
    if A.LANGUAGE_SETTING in changes:
        S.use_language(changes[A.LANGUAGE_SETTING])
    values = {}
    for site in A.sites():
        values[site["name"]] = site["mine"]
        if site.get("api"):
            values[site["name"] + "_api"] = site["api"]["mine"]
    return Response.json({"ok": True, "message": "Сохранено", "values": values, "sites": A.sites(),
                          "language": S.LANGUAGE[0]})


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
