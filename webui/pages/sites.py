"""«Сайты и ключи»: where the parser asks for exact MD5 (the site table: on/off, order,
logins and API keys, own sites, a connection check, cookies), the reverse-search services and
their thresholds, and the parser's own options.

(v5.44.161, the mockups «Настройки» and «Меню») Not a page of the menu any more: the section «Парсер и сайты» of
«Настройки» (:func:`settings_part`); the address /sites opens «Настройки» at it.  The services and the parser's
options are rows of settings saved by the page's one «Сохранить» (their forms say where: ``data-save``)."""
from __future__ import annotations

import re
import time
from pathlib import Path

from webui.html import esc
from webui.server import Request, Response

SAUCENAO_KEY_PAGE = "https://saucenao.com/user.php?page=search-api"
_SERVICES = (("enable_fluffle", "Fluffle", True, "e621, FurAffinity, Twitter, Bluesky, Inkbunny — за секунды"),
             ("enable_e621_iqdb", "e621 IQDB", True,
              "на самом e621: без логина — раз в минуту, с логином и ключом e621 — раз в 6 секунд"),
             ("enable_atf_iqdb", "ATF IQDB", True,
              "поиск похожих на самом ATF (All The Fallen) — нужны логин и API-ключ ATF в таблице сайтов"),
             ("enable_iqdb", "IQDB", True, "iqdb.org: Danbooru, Gelbooru, Yande.re и другие — полминуты и дольше"),
             ("enable_danbooru_iqdb", "Danbooru IQDB", False, "поиск похожих на Danbooru (медленнее)"),
             ("enable_ascii2d", "Ascii2D", False, "японский поиск по картинке, находит pixiv и Twitter"))
_OPTIONS = (("parser_workers", "Файлов одновременно", 3, 1, 8),
            ("parser_reverse_workers", "Картинок к обратному поиску готовится одновременно", 2, 1, 4),
            ("parser_md5_batch_size", "MD5 в одном пакетном запросе", 40, 2, 100),
            ("parser_retry_deferred_minutes", "Ждать отложенные в конце запуска, мин", 15, 0, 120))
_COOKIE_LINE = re.compile(r"^\S+\t(TRUE|FALSE)\t\S*\t(TRUE|FALSE)\t\d+\t\S+\t", re.I)


def register(router) -> None:
    router.add("GET", r"/sites", sites_page)
    router.add("POST", r"/api/sites/row", save_row)
    router.add("POST", r"/api/sites/toggle", toggle_row)
    router.add("POST", r"/api/sites/move", move_row)
    router.add("POST", r"/api/sites/delete", delete_row)
    router.add("POST", r"/api/sites/restore", restore_rows)
    router.add("POST", r"/api/sites/check", check_row)
    router.add("POST", r"/api/sites/cookies", save_cookies)
    router.add("POST", r"/api/sites/services", save_services)
    router.add("POST", r"/api/sites/options", save_options)


def _save(changes: dict) -> Response | None:
    from webui import settings_store
    if not settings_store.save(changes):
        return Response.json({"ok": False, "error": "Программа не ответила — изменения не сохранены"}, 503)
    return None


def _table(req: Request):
    from core import site_table
    return site_table.load(req.settings)


def _row_or_error(entries: dict, body: dict):
    entry_id = str(body.get("id") or "")
    if entry_id not in entries:
        return None, Response.json({"ok": False, "error": "Такого сайта в таблице нет"}, 404)
    return entry_id, None


def _cookie_file(domain: str) -> Path:
    from core.paths import BROWSER_COOKIES_DIR
    return BROWSER_COOKIES_DIR / f"{domain}.txt"


# ── the page ───────────────────────────────────────────────────────────────
def _site_row(cfg: dict, first: bool, last: bool) -> str:
    from core.site_table import engine_label
    entry_id = cfg["_id"]
    name = str(cfg.get("name") or cfg.get("domain") or "")
    domain = str(cfg.get("domain") or "")
    access = []
    if cfg.get("login"):
        access.append("логин " + esc(cfg["login"]))
    if str(cfg.get("api_key") or "").strip():
        access.append("ключ есть")
    if cfg.get("user_id"):
        access.append("ID " + esc(cfg["user_id"]))
    if domain and _cookie_file(domain).is_file():
        access.append("cookies")
    checked = " checked" if cfg.get("enabled", True) else ""
    up = (f'<button type="button" class="icon" data-action="site-move" data-id="{esc(entry_id)}" data-delta="-1" '
          f'aria-label="Выше" title="Спрашивать раньше"{" disabled" if first else ""}>↑</button>')
    down = (f'<button type="button" class="icon" data-action="site-move" data-id="{esc(entry_id)}" data-delta="1" '
            f'aria-label="Ниже" title="Спрашивать позже"{" disabled" if last else ""}>↓</button>')
    own = ' <span class="pill">свой</span>' if cfg.get("_custom") else ""
    entry = "".join(f'<span class="site-chip">{a}</span>' for a in access) or '<span class="site-chip off">без входа</span>'
    return (f'<tr data-id="{esc(entry_id)}" class="{"" if checked else "off"}">'
            f'<td class="row-switch"><input type="checkbox" class="switch" data-site-toggle data-id="{esc(entry_id)}"{checked} '
            f'aria-label="Спрашивать {esc(name)}"></td>'
            f'<td class="row-main"><b>{esc(name)}</b>{own}{"" if name == domain else "<br><small>" + esc(domain) + "</small>"}</td>'
            f'<td data-label="движок">{esc(engine_label(cfg))}</td>'
            f'<td data-label="вход"><span class="site-chips">{entry}</span></td>'
            f'<td class="site-check" aria-live="polite"></td>'
            f'<td class="row-tools"><button type="button" data-action="site-edit" data-id="{esc(entry_id)}">Изменить</button>'
            f'<span class="move-pair">{up}{down}</span></td></tr>')


def _site_json(cfg: dict) -> dict:
    """What the edit form shows: everything but the key itself (only whether there is one)."""
    from core.site_table import engine_label
    return {"id": cfg["_id"], "custom": bool(cfg.get("_custom")), "enabled": bool(cfg.get("enabled", True)),
            "name": str(cfg.get("name") or ""), "domain": str(cfg.get("domain") or ""),
            "base_url": str(cfg.get("base_url") or ""), "login_url": str(cfg.get("login_url") or ""),
            "engine_label": engine_label(cfg), "login": str(cfg.get("login") or ""),
            "has_api_key": bool(str(cfg.get("api_key") or "").strip()), "user_id": str(cfg.get("user_id") or ""),
            "description": str(cfg.get("description") or cfg.get("notes") or "")}


def _spread_hours(settings: dict) -> int:
    """«Растянуть суточный лимит SauceNAO на, ч»: the governor's horizon (core/services/saucenao_quota.py) in hours."""
    from core.services.saucenao_quota import spread_seconds_for
    return max(1, min(24, round(spread_seconds_for(settings) / 3600)))


def _saucenao_state(settings: dict) -> str:
    try:
        from core.services.service_state import get_cooldown
        state = get_cooldown(settings, "saucenao")
    except Exception as exc:          # the state is a hint on the page, not something to fail on
        return f"состояние недоступно ({type(exc).__name__})"
    now = int(time.time())
    parts = []
    short_rem, long_rem = state.get("short_remaining"), state.get("long_remaining")
    if short_rem is not None and int(short_rem) >= 0:
        parts.append(f"осталось за 30 с: {int(short_rem)}")
    if long_rem is not None and int(long_rem) >= 0:
        parts.append(f"за сутки: {int(long_rem)}")
    left = max(0, int(state.get("cooldown_until", 0) or 0) - now)
    parts.append(f"пауза ещё {left // 60} мин" if left else "готов")
    return " · ".join(parts) if len(parts) > 1 else (parts[0] + " (лимиты станут известны после первого запроса)")


def sites_page(req: Request) -> Response:
    """/sites — «Настройки» opened at «Парсер и сайты» (the address of the page before; links and bookmarks live)."""
    from webui.pages import settings
    return settings.settings_page(req, "sites")


def settings_part(req: Request) -> str:
    """The section «Парсер и сайты» of «Настройки»: the table of the sites, «Обратный поиск» and «Парсер» as rows."""
    import json
    from core.site_table import ENGINE_TYPES
    from webui.settings_form import row_html
    s = req.settings
    entries, order, deleted = _table(req)
    rows = [entries[k] for k in order if k in entries]
    table = "".join(_site_row(cfg, i == 0, i == len(rows) - 1) for i, cfg in enumerate(rows))
    restore = (f'<button type="button" data-action="site-restore">Вернуть стандартные ({len(deleted)})</button>'
               if deleted else "")

    def switch(key: str, on: bool, label: str, note: str, note_html: str = "", more: str = "") -> str:
        return row_html(label, note, f'<input type="checkbox" class="switch" id="sv-{key}" name="{key}"'
                                     f'{" checked" if on else ""}>', fid=f"sv-{key}", key=key, hint_html=note_html,
                        more=more)

    def number(key: str, label: str, note: str, value, lo: int, hi: int, more: str = "") -> str:
        return row_html(label, note, f'<input type="number" id="sv-{key}" name="{key}" min="{lo}" max="{hi}" '
                                     f'value="{esc(value)}">', fid=f"sv-{key}", key=key, more=more)
    services = "".join(switch(key, bool(s.get(key, default)), label, note) for key, label, default, note in _SERVICES)
    services += switch("enable_saucenao", bool(s.get("enable_saucenao", True)), "SauceNAO",
                       "лучший для артов с pixiv и Twitter; нужен свой ключ (бесплатный)")
    has_key = bool(str(s.get("saucenao_api_key") or "").strip())
    first = bool(s.get("reverse_saucenao_first", False))
    services += row_html(
        "Ключ SauceNAO", "", '<input type="password" id="saucenao-key" name="saucenao_api_key" autocomplete="off" '
        f'spellcheck="false" placeholder="{"ключ сохранён — оставьте пустым, чтобы не менять" if has_key else "вставьте ключ"}">',
        fid="saucenao-key", key="saucenao_api_key",
        hint_html=f'{esc(_saucenao_state(s))} · <a href="{SAUCENAO_KEY_PAGE}" target="_blank" rel="noopener">где взять ключ</a>')
    services += row_html(
        "Порядок сервисов", "файл идёт от сервиса к сервису по очереди, пока какой-нибудь его не найдёт",
        '<select id="reverse-order" name="reverse_order">'
        f'<option value="iqdb"{"" if first else " selected"}>IQDB первым — бережёт лимит SauceNAO</option>'
        f'<option value="saucenao"{" selected" if first else ""}>SauceNAO первым — быстрее, пока хватает лимита</option>'
        '</select>', fid="reverse-order", key="reverse_order",
        more="Сначала быстрые (Fluffle, e621 IQDB — секунды), потом IQDB (полминуты и дольше). При «IQDB первым» "
             "SauceNAO ищет только то, что не нашли остальные: поисков на сутки у него не больше лимита аккаунта.")
    services += number("saucenao_spread_hours", "Растянуть суточный лимит SauceNAO на, ч",
                       "1 — как можно быстрее; 24 — поровну на весь день, медленно", _spread_hours(s), 1, 24,
                       more="1 — с той скоростью, что разрешает ваш аккаунт за 30 секунд, оставляя на сутки запас в "
                            f"{_int(s.get('saucenao_quota_reserve'), 10, 0, 10000)} поисков.")
    services += number("min_similarity", "Порог похожести, %", "SauceNAO, Ascii2D, Danbooru IQDB",
                       _int(s.get("min_similarity"), 85, 50, 99), 50, 99)
    services += number("iqdb_min_similarity", "Порог IQDB, %", "IQDB и e621 IQDB",
                       _int(s.get("iqdb_min_similarity"), 75, 50, 99), 50, 99)
    from core import e621_export
    options = switch("skip_existing", bool(s.get("skip_existing", True)), "Пропускать разобранные",
                     "файлы, которые парсер уже разобрал, не открываются снова")
    options += switch("parser_md5_batch_enabled", bool(s.get("parser_md5_batch_enabled", True)), "Пакетные MD5-запросы",
                      "несколько файлов одним запросом — на сайтах, которые отвечают на него правильно",
                      more="Только на сайтах, которые проверенно отвечают правильно: проверка — по найденному раньше "
                           "или по новым постам сайта.")
    options += switch("parser_reverse_remember", bool(s.get("parser_reverse_remember", True)),
                      "Помнить ответы обратного поиска",
                      "сервис, который уже искал файл, о нём больше не спрашивается",
                      more="Нашёл он или нет: «Искать снова» Брака спрашивает только тех, кто файл ещё не искал "
                           "(новые, выключенные, со сбоем). Выключите, чтобы спросить всех заново.")
    options += switch("e621_export_enabled", bool(s.get("e621_export_enabled", False)), "MD5 на e621 — по его выгрузке",
                      "e621 просит не искать десятки тысяч MD5 через API, а брать его ежедневную выгрузку",
                      note_html=f'Сейчас: <span data-e621-export>{esc(e621_export.status(s)["text"])}</span>',
                      more="Программа скачает её (около 2 ГБ, на диск с архивом, раз в 30 дней) и будет знать, какие "
                           "файлы есть на e621, сама — сайт спрашивается только о найденных и о новых постах (раз в "
                           "час).")
    options += "".join(number(key, label, "", _int(s.get(key), default, lo, hi), lo, hi)
                       for key, label, default, lo, hi in _OPTIONS)
    engines = "".join(f'<option value="{esc(label)}">{esc(label if label != "Свой" else "Свой — определить по сайту")}</option>'
                      for label in ENGINE_TYPES)
    data = {e["_id"]: _site_json(e) for e in rows}
    data_json = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")     # no «</script>» inside
    finder = ('<input type="search" class="row-filter" data-row-filter="#sites-rows" placeholder="Найти сайт" '
              'aria-label="Найти сайт" autocomplete="off" spellcheck="false">') if len(rows) >= 6 else ""
    on = sum(1 for cfg in rows if cfg.get("enabled", True))
    hidden_save = ('<button type="submit" class="form-submit" hidden tabindex="-1">Сохранить</button>'
                   '<p class="form-error" role="alert" hidden></p>')
    return f"""
<section class="settings-block sx-block sx-sites" id="sites" data-title="Сайты для поиска по MD5">
  <h3 class="sx-title" id="sites-title">Сайты для поиска по MD5 <small>включено {on} из {len(rows)} · спрашиваются сверху вниз</small></h3>
  <p class="sx-lead">Где парсер ищет точный MD5 и чем ищет похожие картинки, когда MD5 не нашёлся. Ключи хранятся
    только в настройках программы.</p>
  <div class="sx-group sx-box">
    {finder}
    <table class="sites-table rows" id="sites-rows" aria-labelledby="sites-title">
      <thead><tr><th><span class="sr-only">Вкл</span></th><th>Сайт</th><th>Движок</th><th>Вход</th><th>Проверка</th><th></th></tr></thead>
      <tbody>{table}</tbody>
    </table>
    <div class="tool-actions">
      <button type="button" data-action="site-add">Добавить сайт</button>
      <button type="button" data-action="site-check-all">Проверить включённые</button>
      {restore}
      <span class="action-status" role="status"></span>
    </div>
  </div>

  <dialog class="site-dialog" aria-labelledby="site-dialog-title">
    <form method="dialog" data-form="site">
      <h2 id="site-dialog-title">Сайт</h2>
      <input type="hidden" name="id">
      <label class="check"><input type="checkbox" name="enabled"> Спрашивать этот сайт</label>
      <div class="field-row"><label>Название</label><input type="text" name="name"></div>
      <div class="field-row"><label>Домен</label><input type="text" name="domain" placeholder="gelbooru.com" required></div>
      <div class="field-row"><label>Адрес сайта</label><input type="text" name="base_url" placeholder="https://gelbooru.com"></div>
      <div class="field-row"><label>Движок</label><select name="engine_label">{engines}</select></div>
      <div class="field-row"><label>Логин</label><input type="text" name="login" autocomplete="off"></div>
      <div class="field-row"><label>API-ключ</label><input type="password" name="api_key" autocomplete="off" spellcheck="false">
        <label class="check small"><input type="checkbox" name="api_key_clear"> убрать ключ</label></div>
      <div class="field-row"><label>User ID</label><input type="text" name="user_id" autocomplete="off"></div>
      <div class="field-row"><label>Страница входа</label><input type="text" name="login_url"></div>
      <div class="field-row"><label>Описание</label><input type="text" name="description"></div>
      <details class="cookies"><summary>Cookies (cookies.txt)</summary>
        <p class="hint">Если сайт закрыт Cloudflare или требует вход: выгрузите cookies сайта из браузера в формате
        cookies.txt и вставьте сюда (или нажмите «Передать cookies» в расширении Companion).</p>
        <textarea name="cookies" rows="5" spellcheck="false" placeholder="# Netscape HTTP Cookie File"></textarea>
        <button type="button" data-action="site-cookies">Сохранить cookies</button></details>
      <div class="tool-actions">
        <button type="submit" class="primary" value="save">Сохранить</button>
        <button type="button" data-action="site-dialog-close">Отмена</button>
        <button type="button" class="danger" data-action="site-delete">Убрать из списка</button>
        <span class="action-status" role="status"></span>
      </div>
    </form>
  </dialog>
  <script type="application/json" id="sites-data">{data_json}</script>
</section>
<section class="settings-block sx-block" id="set-services" data-title="Обратный поиск">
  <h3 class="sx-title" id="services-title">Обратный поиск</h3>
  <form class="settings-form" data-form="services" data-save="/api/sites/services">
    <div class="sx-group">{services}</div>{hidden_save}
  </form>
</section>
<section class="settings-block sx-block" id="set-parser" data-title="Парсер">
  <h3 class="sx-title" id="options-title">Парсер</h3>
  <form class="settings-form" data-form="options" data-save="/api/sites/options">
    <div class="sx-group">{options}</div>{hidden_save}
  </form>
</section>"""


def _int(value, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(float(value))))
    except (TypeError, ValueError):
        return default


# ── the site table ─────────────────────────────────────────────────────────
def save_row(req: Request) -> Response:
    from core import site_table
    body = req.json()
    form = body.get("form") if isinstance(body.get("form"), dict) else {}
    if not site_table.clean_domain(form.get("domain") or form.get("base_url") or ""):
        return Response.json({"ok": False, "error": "Укажите домен сайта"}, 400)
    entries, order, deleted = _table(req)
    entry_id = str(body.get("id") or "")
    if entry_id:
        if entry_id not in entries:
            return Response.json({"ok": False, "error": "Такого сайта в таблице нет"}, 404)
        old = entries[entry_id]
        cfg = site_table.apply_edit(old, form)
        cfg.update({"_id": entry_id, "_custom": bool(old.get("_custom"))})
        if old.get("_custom"):
            cfg["site_id"] = entry_id
        else:
            cfg["builtin_id"] = old.get("builtin_id")
        entries[entry_id] = cfg
    else:
        cfg = site_table.new_custom(form)
        entries[cfg["_id"]] = cfg
        order.append(cfg["_id"])
    error = _save(site_table.changes(req.settings, entries, order, deleted))
    return error or Response.json({"ok": True, "id": cfg["_id"]})


def toggle_row(req: Request) -> Response:
    from core import site_table
    body = req.json()
    entries, order, deleted = _table(req)
    entry_id, error = _row_or_error(entries, body)
    if error:
        return error
    entries[entry_id]["enabled"] = bool(body.get("enabled"))
    error = _save(site_table.changes(req.settings, entries, order, deleted))
    return error or Response.json({"ok": True, "enabled": entries[entry_id]["enabled"]})


def move_row(req: Request) -> Response:
    from core import site_table
    body = req.json()
    entries, order, deleted = _table(req)
    entry_id, error = _row_or_error(entries, body)
    if error:
        return error
    i = order.index(entry_id)
    j = max(0, min(len(order) - 1, i + (1 if int(body.get("delta") or 0) > 0 else -1)))
    order[i], order[j] = order[j], order[i]
    error = _save(site_table.changes(req.settings, entries, order, deleted))
    return error or Response.json({"ok": True})


def delete_row(req: Request) -> Response:
    """A built-in site goes to «removed» (it can come back with «Вернуть стандартные»); the user's
    own site is removed for good."""
    from core import site_table
    body = req.json()
    entries, order, deleted = _table(req)
    entry_id, error = _row_or_error(entries, body)
    if error:
        return error
    cfg = entries.pop(entry_id)
    if not cfg.get("_custom") and cfg.get("builtin_id"):
        deleted.add(str(cfg["builtin_id"]))
    order = [k for k in order if k != entry_id]
    error = _save(site_table.changes(req.settings, entries, order, deleted))
    return error or Response.json({"ok": True})


def restore_rows(req: Request) -> Response:
    from core import site_table
    entries, order, deleted = _table(req)
    back = site_table.restore_builtins(entries, order, deleted)
    error = _save(site_table.changes(req.settings, entries, order, deleted))
    return error or Response.json({"ok": True, "restored": back})


def check_row(req: Request) -> Response:
    from core import site_table
    entries, _order, _deleted = _table(req)
    entry_id, error = _row_or_error(entries, req.json())
    if error:
        return error
    result = site_table.check(req.settings, entries[entry_id])
    return Response.json({"ok": True, "id": entry_id, "site_ok": bool(result.get("ok")),
                          "message": str(result.get("message") or "")})


def save_cookies(req: Request) -> Response:
    """cookies.txt of one site, as the parser reads it (``<domain>.txt`` in the cookies folder)."""
    from core import site_table
    body = req.json()
    domain = site_table.clean_domain(body.get("domain") or "")
    text = str(body.get("text") or "")
    if not domain:
        return Response.json({"ok": False, "error": "Не указан домен сайта"}, 400)
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    good = [ln for ln in lines if _COOKIE_LINE.match(ln) or _COOKIE_LINE.match("#HttpOnly_" + ln)]
    if not good:
        return Response.json({"ok": False, "error": "Это не похоже на cookies.txt (формат Netscape: поля через табуляцию)"}, 400)
    if len(text) > 2_000_000:
        return Response.json({"ok": False, "error": "Слишком большой файл cookies"}, 413)
    target = _cookie_file(domain)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    tmp.replace(target)
    has_cf = any("\tcf_clearance\t" in ln for ln in good)
    return Response.json({"ok": True, "lines": len(good), "cf_clearance": has_cf})


# ── services and options ───────────────────────────────────────────────────
def save_services(req: Request) -> Response:
    body = req.json()
    changes = {key: bool(body.get(key)) for key, _l, _d, _n in _SERVICES}
    changes["enable_saucenao"] = bool(body.get("enable_saucenao"))
    key = str(body.get("saucenao_api_key") or "").strip()
    if key:                                   # empty — keep the stored key
        if not re.fullmatch(r"[0-9A-Za-z]{16,128}", key):
            return Response.json({"ok": False, "error": "Ключ SauceNAO — строка из латинских букв и цифр (обычно 40 знаков)"}, 400)
        changes["saucenao_api_key"] = key
    if body.get("saucenao_api_key_clear"):
        changes["saucenao_api_key"] = ""
    changes["min_similarity"] = _int(body.get("min_similarity"), 85, 50, 99)
    changes["iqdb_min_similarity"] = _int(body.get("iqdb_min_similarity"), 75, 50, 99)
    if body.get("saucenao_spread_hours") is not None:
        changes["saucenao_quota_spread_seconds"] = _int(body.get("saucenao_spread_hours"), 1, 1, 24) * 3600
    if body.get("reverse_order") in ("saucenao", "iqdb"):
        changes["reverse_saucenao_first"] = body.get("reverse_order") == "saucenao"
    error = _save(changes)
    return error or Response.json({"ok": True})


def save_options(req: Request) -> Response:
    body = req.json()
    changes = {key: _int(body.get(key), default, lo, hi) for key, _l, default, lo, hi in _OPTIONS}
    changes["skip_existing"] = bool(body.get("skip_existing"))
    changes["parser_md5_batch_enabled"] = bool(body.get("parser_md5_batch_enabled"))
    if "parser_reverse_remember" in body:        # the page's form always sends it; an older caller — not touched
        changes["parser_reverse_remember"] = bool(body.get("parser_reverse_remember"))
    if "e621_export_enabled" in body:            # (v5.44.25) the same
        changes["e621_export_enabled"] = bool(body.get("e621_export_enabled"))
    error = _save(changes)
    return error or Response.json({"ok": True})
