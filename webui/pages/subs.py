"""The page «Подписки»: the subscriptions (on/off, add, edit, check now, reset progress, delete),
the check in progress (journal, files downloaded, the large-download question), «Скачать тег»
(one download of a tag without a subscription) and the blocklist shared with the grabber.

Checks run as core jobs (``core/subscription_jobs.py``): one at a time for the whole program, the
old Qt page and the automatic check included.
"""
from __future__ import annotations

import json
import re
import time

from webui.html import esc, page_frame
from webui.server import Request, Response

TAG_LIMIT_MAX = 10000          # posts per site one «Скачать тег» may take
_RUNNING = {"starting", "running", "waiting", "stopping"}


def register(router) -> None:
    router.add("GET", r"/subs", subs_page)
    router.add("GET", r"/api/subs/state", subs_state)
    router.add("GET", r"/api/subs/sub", one_sub)
    router.add("POST", r"/api/subs/save", save_sub)
    router.add("POST", r"/api/subs/toggle", toggle_sub)
    router.add("POST", r"/api/subs/delete", delete_sub)
    router.add("POST", r"/api/subs/reset", reset_sub)
    router.add("POST", r"/api/subs/run", run_subs)
    router.add("POST", r"/api/subs/stop", stop_subs)
    router.add("POST", r"/api/subs/decide", decide_plan)
    router.add("POST", r"/api/subs/tag", download_tag)
    router.add("POST", r"/api/subs/blocklist", save_blocklist)
    router.add("GET", r"/api/subs/sessions", sub_sessions)
    router.add("POST", r"/api/subs/clear/stop", clear_stop)
    router.add("POST", r"/api/subs/clear", clear_sessions)
    router.add("GET", r"/api/subs/clear/state", clear_state)


def _prefs() -> dict:
    from webui import prefs
    stored = prefs.load()
    return {"thumbs": stored.get("thumbs", "m")}         # the look is the page's own (page_frame)


def _when(ts: int) -> str:
    if not ts:
        return ""
    now = time.localtime()
    t = time.localtime(ts)
    if t.tm_year == now.tm_year and t.tm_yday == now.tm_yday:
        return time.strftime("сегодня %H:%M", t)
    return time.strftime("%d.%m %H:%M", t)


def _row_state(sub: dict, running: set) -> dict:
    """What a row shows about the checks of a subscription (also sent while a check is going)."""
    from core.subscriptions import next_check_at
    last = int(sub.get("last_check", 0) or 0)
    if not sub.get("enabled", True):
        nxt = "выключена"
    elif not last:
        nxt = "при ближайшей автопроверке"
    else:
        at = next_check_at(sub)
        nxt = "пора" if at <= time.time() else _when(at)
    from core.redaction import sanitize_text
    return {"last": _when(last) or "ещё не было", "next": nxt, "downloaded": int(sub.get("downloaded_count", 0) or 0),
            "error": sanitize_text(sub.get("last_error") or ""), "running": str(sub.get("id")) in running,
            "enabled": bool(sub.get("enabled", True))}


def _sub_row(sub: dict, state: dict) -> str:
    """One subscription: its switch, what it asks where, when it was and will be checked, what it brought, its
    buttons (v5.37: a row of a list — on a phone a card, ``data-label`` names each value)."""
    from core.subscriptions import normalize_sites
    sub_id = str(sub.get("id") or "")
    query = str(sub.get("query") or sub.get("name") or "")
    sites = "".join(f'<span class="site-chip">{esc(s["site"])}</span>' for s in normalize_sites(sub)) or \
        '<span class="site-chip off">нет сайтов</span>'
    checked = " checked" if state["enabled"] else ""
    classes = " ".join(c for c in ("off" if not state["enabled"] else "", "running" if state["running"] else "") if c)
    return (f'<tr data-id="{esc(sub_id)}" class="{classes}">'
            f'<td class="row-switch"><input type="checkbox" class="switch" data-sub-toggle data-id="{esc(sub_id)}"{checked} '
            f'aria-label="Проверять «{esc(query)}» автоматически"></td>'
            f'<td class="row-main"><b>{esc(query)}</b><span class="site-chips">{sites}</span>'
            f'<small class="sub-error" data-cell="error"{"" if state["error"] else " hidden"}>{esc(state["error"])}</small></td>'
            f'<td data-label="каждые">{int(sub.get("check_interval_hours", 24) or 24)} ч</td>'
            f'<td data-cell="last" data-label="проверена">{esc(state["last"])}</td>'
            f'<td data-cell="next" data-label="следующая">{esc(state["next"])}</td>'
            f'<td class="row-number" data-cell="downloaded" data-label="скачано">{state["downloaded"]}</td>'
            f'<td class="row-tools"><button type="button" data-action="sub-run" data-id="{esc(sub_id)}">Проверить</button>'
            f'<button type="button" data-action="sub-edit" data-id="{esc(sub_id)}">Изменить</button></td></tr>')


def _stats(subs: list) -> str:
    """The numbers over the list: how many, how many check themselves, what they brought, the next check."""
    from core.subscriptions import next_check_at
    if not subs:
        return ""
    on = [sub for sub in subs if sub.get("enabled", True)]
    total = sum(int(sub.get("downloaded_count", 0) or 0) for sub in subs)
    checked = [next_check_at(sub) for sub in on if int(sub.get("last_check", 0) or 0)]
    if len(checked) < len(on):
        soon = "скоро" if on else "—"           # (a subscription never checked: at the next automatic check)
    else:
        at = min(checked) if checked else 0
        soon = ("пора" if at <= time.time() else _when(at)) if at else "—"
    return ('<dl class="stats">'
            f'<div><dt>подписок</dt><dd>{len(subs)}</dd></div>'
            f'<div><dt>проверяются сами</dt><dd>{len(on)}</dd></div>'
            f'<div><dt>скачано всего</dt><dd>{total}</dd></div>'
            f'<div><dt>ближайшая проверка</dt><dd>{esc(soon)}</dd></div></dl>')


def _sub_json(sub: dict) -> dict:
    from core.subscriptions import normalize_sites
    return {"id": str(sub.get("id") or ""), "query": str(sub.get("query") or sub.get("name") or ""),
            "sites": [{"site": s["site"], "priority": int(s.get("priority", 3) or 3),
                       "query_override": str(s.get("query_override") or "")} for s in normalize_sites(sub)],
            "check_interval_hours": int(sub.get("check_interval_hours", 24) or 24),
            "max_pages": int(sub.get("max_pages", 3) or 3), "run_mode": str(sub.get("run_mode") or "all"),
            "run_direction": str(sub.get("run_direction") or "newest_to_oldest"),
            "enabled": bool(sub.get("enabled", True))}


def _parser_sites(settings: dict, known: list) -> list:
    """Known tag-search sites the parser has switched on: the default choice of a new subscription."""
    cfg = settings.get("sites") or {}
    return [d for d in known if isinstance(cfg.get(d), dict) and cfg[d].get("enabled")]


def _auto_note() -> str:
    from core import background_duties, subscription_jobs
    from webui import service
    if not background_duties.running() and service.bridge() is None:     # no program window: a browser only
        return ("Автопроверка работает, пока открыта программа (окно Local Booru); "
                "отсюда подписки можно проверить вручную.")
    note = "Автопроверка: раз в минуту программа смотрит, каким включённым подпискам пора."
    paused = subscription_jobs.auto_paused_until()
    if paused:
        note += f" Сейчас она отдыхает до {time.strftime('%H:%M', time.localtime(paused))} после «Остановить» — «Проверить» её будит."
    return note


def subs_page(req: Request) -> Response:
    from core import subscription_jobs
    from core.download_policy import downloads_enabled
    from core.subscription_forms import scannable_sites
    from core.subscriptions import DEFAULT_SITE_PRIORITY, load_subscriptions
    s = req.settings
    subs = load_subscriptions()
    running = subscription_jobs.running_ids()
    rows = "".join(_sub_row(sub, _row_state(sub, running)) for sub in subs)
    empty = "" if subs else ('<div class="empty">Подписок пока нет. «Добавить подписку» — и программа будет сама '
                             'скачивать новые работы тега или автора.</div>')
    finder = ('<input type="search" class="row-filter" data-row-filter="#subs-rows" placeholder="Найти подписку" '
              'aria-label="Найти подписку" autocomplete="off" spellcheck="false">') if len(subs) >= 6 else ""
    known = scannable_sites()
    on = _parser_sites(s, known)
    picks = "".join(
        f'<label class="check"><input type="checkbox" name="site" value="{esc(d)}"{" checked" if d in on else ""}> {esc(d)}</label>'
        for d in known)
    policy = ("" if downloads_enabled(s) else
              '<p class="tool-warn">Скачивание файлов выключено на этот сеанс (выбрано при запуске программы): '
              'подписки находят посты, а скачаются они после перезапуска с включённым скачиванием.</p>')
    limit = max(1, min(TAG_LIMIT_MAX, int(s.get("grabber_tag_download_limit", 500) or 500)))
    blocklist = str(s.get("grabber_subscriptions_blocklist") or s.get("downloader_blocklist") or "")
    data = {"sites": known, "parser_on": on, "priority": {d: DEFAULT_SITE_PRIORITY.get(d, 3) for d in known},
            "subs": {str(sub.get("id")): _sub_json(sub) for sub in subs if sub.get("id")}}
    data_json = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")     # no «</script>» inside
    body = f"""
<section class="tool tool-wide settings" id="subs" data-state-url="/api/subs/state">
  <header class="tool-head">
    <h1>Подписки</h1>
    <p class="tool-lead">Программа сама проверяет подписки и скачивает новые работы — только оригиналы. Файл, который уже есть
    в библиотеке, не скачивается снова: к нему добавляются теги и ссылки с нового сайта.</p>
    <div class="tool-actions head-actions" role="group" aria-label="Проверка подписок">
      <button type="button" data-action="sub-add">Добавить подписку</button>
      <button type="button" class="primary" data-action="subs-run-all"{"" if subs else " disabled"}>Проверить все</button>
      <button type="button" class="danger" data-action="subs-stop" disabled>Остановить</button>
      <span class="action-status" role="status"></span>
    </div>
  </header>
  {policy}
  <div class="tool-board">
    <div class="board-main">
      <section class="panel subs-list" aria-labelledby="subs-title">
        <div class="panel-head"><h2 id="subs-title">Мои подписки</h2>{finder}</div>
        {_stats(subs)}
        <table class="subs-table rows" id="subs-rows">
          <thead><tr><th><span class="sr-only">Авто</span></th><th>Подписка</th><th>Каждые</th><th>Проверена</th>
            <th>Следующая</th><th class="row-number">Скачано</th><th></th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        {empty}
        <p class="hint auto-note">{esc(_auto_note())}</p>
        <p class="hint">Подписаться можно и прямо на сайте: страница поиска по тегам → панель расширения «LB» у
        правого края → «Подписаться».</p>
      </section>

      <div class="job panel" data-job hidden>
        <div class="job-head"><b class="job-title"></b> <span class="job-status"></span></div>
        <dl class="job-counts">
          <div><dt>скачано файлов</dt><dd data-count="downloaded">0</dd></div>
          <div data-show="many" hidden><dt>подписка</dt><dd data-count="index"></dd></div>
          <div><dt data-elapsed-label>идёт</dt><dd data-count="elapsed"></dd></div>
        </dl>
        <p class="job-current"></p>
        <div class="plan-box" role="group" aria-labelledby="plan-title" hidden>
          <p id="plan-title"><b>Большая загрузка</b> <span class="plan-name"></span></p>
          <p class="plan-text"></p>
          <div class="tool-actions">
            <button type="button" class="primary" data-action="subs-plan-yes">Скачать</button>
            <button type="button" data-action="subs-plan-no">Не скачивать</button>
            <span class="action-status" role="status"></span>
          </div>
        </div>
        <div class="job-links" hidden>Новые файлы — в <a href="/posts">галерее</a>.</div>
        <pre class="job-log" tabindex="0" aria-label="Журнал проверки"></pre>
      </div>
    </div>

    <aside class="board-side">
      <section class="panel" aria-labelledby="tag-title">
        <h2 id="tag-title">Скачать тег один раз</h2>
        <form class="settings-form tag-form" data-form="tag">
          <div class="field-row stacked"><label for="tag-query">Тег или запрос</label>
            <input type="text" id="tag-query" name="query" required spellcheck="false" autocomplete="off" placeholder="например, artist_name"></div>
          <fieldset class="site-picks chips"><legend>Сайты</legend>{picks or '<span class="tool-warn">нет сайтов с поиском по тегам</span>'}</fieldset>
          <div class="field-row"><label for="tag-limit">Не больше с каждого сайта</label>
            <input type="number" id="tag-limit" name="limit" min="1" max="{TAG_LIMIT_MAX}" value="{limit}"> <small>постов, самые новые</small></div>
          <p class="hint">Подписка не создаётся. Если остановить загрузку, следующий запуск с тем же тегом и сайтами продолжит с того же места.</p>
          <div class="tool-actions">
            <button type="submit" class="primary">Скачать</button>
            <button type="button" data-action="tag-to-sub">Сделать подпиской</button>
            <span class="action-status" role="status"></span>
          </div>
        </form>
      </section>

      <section class="panel" aria-labelledby="block-title">
        <h2 id="block-title">Блоклист</h2>
        <form class="settings-form" data-form="blocklist">
          <p class="hint">Посты с этими тегами не скачиваются — ни подписками, ни граббером. Теги через пробел, запятую или с
          новой строки; тег проверяется по всем сайтам, где нашёлся тот же файл. Расширение в браузере размывает или
          прячет такие посты и на самих сайтах.</p>
          <textarea name="blocklist" rows="4" spellcheck="false" aria-label="Блоклист">{esc(blocklist)}</textarea>
          <div class="tool-actions"><button type="submit" class="primary">Сохранить</button><span class="action-status" role="status"></span></div>
        </form>
      </section>
    </aside>
  </div>

  <dialog class="site-dialog sub-dialog" aria-labelledby="sub-dialog-title">
    <form method="dialog" data-form="sub">
      <h2 id="sub-dialog-title">Подписка</h2>
      <input type="hidden" name="id">
      <div class="field-row"><label for="sub-query">Тег или запрос</label>
        <input type="text" id="sub-query" name="query" required spellcheck="false" autocomplete="off"></div>
      <table class="pick-table">
        <thead><tr><th></th><th>Сайт</th><th>Приоритет</th><th>Свой запрос для сайта</th></tr></thead>
        <tbody></tbody>
      </table>
      <p class="hint">Файл скачивается с сайта с бо́льшим приоритетом (5 — первым); теги собираются со всех сайтов, где нашёлся тот же файл.</p>
      <div class="numbers">
        <label class="num"><span>Проверять каждые, ч</span><input type="number" name="check_interval_hours" min="1" max="720"></label>
        <label class="num"><span>Страниц за проверку</span><input type="number" name="max_pages" min="1" max="50"></label>
      </div>
      <div class="field-row"><label for="sub-mode">Что скачивать</label>
        <select id="sub-mode" name="run_mode">
          <option value="all">всё найденное</option>
          <option value="new">только новые (первая проверка запоминает, с какого поста начинать)</option>
          <option value="old">только старые</option>
        </select></div>
      <div class="field-row"><label for="sub-dir">Порядок</label>
        <select id="sub-dir" name="run_direction">
          <option value="newest_to_oldest">от новых к старым</option>
          <option value="oldest_to_newest">от старых к новым</option>
        </select></div>
      <label class="check"><input type="checkbox" name="enabled"> Проверять автоматически</label>
      <div class="tool-actions">
        <button type="submit" class="primary">Сохранить</button>
        <button type="button" data-action="sub-dialog-close">Отмена</button>
        <button type="button" data-action="sub-reset">Сбросить прогресс</button>
        <button type="button" data-action="sub-clear-open">Удалить скачанные…</button>
        <button type="button" class="danger" data-action="sub-delete">Удалить подписку</button>
        <span class="action-status" role="status"></span>
      </div>
    </form>
  </dialog>
  <dialog class="site-dialog clear-dialog" aria-labelledby="clear-title">
    <form method="dialog" data-form="clear">
      <h2 id="clear-title">Удалить скачанные</h2>
      <input type="hidden" name="id">
      <p class="hint">Файлы выбранных сессий (каждая проверка подписки — своя папка) уходят в корзину: их можно вернуть,
      сначала делается копия базы. Скачанное не скачается снова — его помнят корзина и список подписки.</p>
      <fieldset class="clear-modes"><legend>Что удалить</legend>
        <label class="check"><input type="radio" name="mode" value="all" checked> все сессии</label>
        <label class="check"><input type="radio" name="mode" value="keep_last"> все, кроме последней у каждого сайта</label>
        <label class="check"><input type="radio" name="mode" value="pick"> выбранные ниже</label>
      </fieldset>
      <p class="tool-warn clear-shared" hidden></p>
      <ul class="plain-list clear-sessions"></ul>
      <div class="tool-actions">
        <button type="button" class="danger" data-action="sub-clear">В корзину</button>
        <button type="button" data-action="sub-clear-close">Закрыть</button>
        <span class="action-status" role="status"></span>
      </div>
      <div class="job" data-clear-job hidden>
        <div class="job-head"><b class="job-title"></b> <span class="job-status"></span>
          <button type="button" class="link" data-action="sub-clear-stop" hidden>Остановить</button></div>
        <pre class="job-log" tabindex="0" aria-label="Журнал"></pre>
      </div>
    </form>
  </dialog>
  <script type="application/json" id="subs-data">{data_json}</script>
</section>"""
    return Response.html(page_frame("Подписки", body, active="subs", subnav=False, kind="tool",
                                    body_data={"tool": "subs"}, **_prefs()))


# ── the check in progress ──────────────────────────────────────────────────
def subs_state(req: Request) -> Response:
    from core import subscription_jobs
    from core.subscriptions import load_subscriptions
    running = subscription_jobs.running_ids()
    rows = {str(s.get("id")): _row_state(s, running) for s in load_subscriptions() if s.get("id")}
    paused = subscription_jobs.auto_paused_until()
    extra = {"subs": rows, "auto_paused_until": int(paused), "auto_note": _auto_note()}
    job = subscription_jobs.current()
    if job is None:
        busy = subscription_jobs.owner()
        return Response.json({"ok": True, "status": "busy" if busy else "idle", "owner": busy, **extra})
    snap = job.snapshot(after=req.int_arg("after", 0, lo=0))
    return Response.json({"ok": True, **snap, **extra})


def _busy(exc) -> Response:
    return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или остановите."}, 409)


def _start(req: Request, subs: list, **kwargs) -> Response:
    from core import subscription_jobs
    try:
        job = subscription_jobs.start(dict(req.settings), subs, **kwargs)
    except subscription_jobs.SubscriptionsBusy as exc:
        return _busy(exc)
    return Response.json({"ok": True, "id": job.id})


def run_subs(req: Request) -> Response:
    from core.subscriptions import get_subscription, load_subscriptions, normalize_sites
    body = req.json()
    if body.get("all"):
        subs = [s for s in load_subscriptions() if s.get("enabled", True)]
        if not subs:
            return Response.json({"ok": False, "error": "Нет включённых подписок"}, 400)
        return _start(req, subs, label="Все подписки")
    sub = get_subscription(str(body.get("id") or ""))
    if sub is None:
        return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
    sites = ", ".join(x["site"] for x in normalize_sites(sub))
    return _start(req, [sub], label=f"Подписка: {sub.get('query') or sub.get('name')} @ {sites}")


def stop_subs(req: Request) -> Response:
    from core import subscription_jobs
    job = subscription_jobs.current()
    if job is None or job.status not in _RUNNING - {"stopping"}:
        return Response.json({"ok": False, "error": "Подписки сейчас не проверяются"}, 409)
    job.stop()
    return Response.json({"ok": True, "status": job.status})


def decide_plan(req: Request) -> Response:
    """«Скачать» / «Не скачивать» for the question the page showed: ``job`` and ``seq`` name it (both are needed).
    A page that was late to see that its question is gone does not answer the next one (of the same check or of
    the next), and a question answered already (in the program window) is not answered again."""
    from core import subscription_jobs
    body = req.json()
    job = subscription_jobs.current()
    if job is None or job.status != "waiting":
        return Response.json({"ok": False, "error": "Сейчас ничего не спрашивается"}, 409)
    asked_job, asked_seq = _whole(body.get("job")), _whole(body.get("seq"))
    if asked_job != job.id or asked_seq is None or not job.decide(bool(body.get("allow")), seq=asked_seq):
        return Response.json({"ok": False, "error": "Этот вопрос уже не задаётся — посмотрите, о чём спрашивают сейчас"}, 409)
    return Response.json({"ok": True})


def _whole(value) -> int | None:
    """A whole number the page sent (a number or its ASCII digits), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value.strip()):
        return int(value.strip())
    return None


# ── the subscriptions ──────────────────────────────────────────────────────
def one_sub(req: Request) -> Response:
    """The subscription as it is now, for the dialog (the page's copy is as of its loading: saving
    from it put back an «Авто» switched in the table or an edit made on the old page)."""
    from core.subscriptions import get_subscription
    sub = get_subscription(req.arg("id"))
    if sub is None:
        return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
    return Response.json({"ok": True, "sub": _sub_json(sub)})


def _not_while_running(sub_id: str) -> Response | None:
    from core import subscription_jobs
    if sub_id in subscription_jobs.running_ids():
        return Response.json({"ok": False, "error": "Эта подписка сейчас проверяется — остановите проверку или дождитесь конца"}, 409)
    return None


def save_sub(req: Request) -> Response:
    from core.subscription_forms import clean_form
    from core.subscriptions import add_subscription, get_subscription, update_subscription
    body = req.json()
    fields, error = clean_form(body.get("form") if isinstance(body.get("form"), dict) else {})
    if error:
        return Response.json({"ok": False, "error": error}, 400)
    sub_id = str(body.get("id") or "")
    if sub_id:
        if get_subscription(sub_id) is None:
            return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
        update_subscription(sub_id, **fields)
        return Response.json({"ok": True, "id": sub_id})
    enabled = fields.pop("enabled")
    sub = add_subscription(fields.pop("name"), fields.pop("sites"), fields.pop("query"), **fields)
    if not enabled:
        update_subscription(sub["id"], enabled=False)
    return Response.json({"ok": True, "id": sub["id"]})


def toggle_sub(req: Request) -> Response:
    from core.subscriptions import update_subscription
    body = req.json()
    if not update_subscription(str(body.get("id") or ""), enabled=bool(body.get("enabled"))):
        return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
    return Response.json({"ok": True})


def delete_sub(req: Request) -> Response:
    from core.subscriptions import delete_subscription
    sub_id = str(req.json().get("id") or "")
    refused = _not_while_running(sub_id)
    if refused:
        return refused
    if not delete_subscription(sub_id):
        return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
    return Response.json({"ok": True})


def reset_sub(req: Request) -> Response:
    """«Сбросить прогресс»: the next check looks through the posts from the newest again (files
    already in the library are not downloaded again)."""
    from core.subscriptions import update_subscription
    sub_id = str(req.json().get("id") or "")
    refused = _not_while_running(sub_id)
    if refused:
        return refused
    if not update_subscription(sub_id, last_post_ids={}, oldest_post_ids={}, last_check=0, last_error=""):
        return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
    return Response.json({"ok": True})


# ── «Скачать тег» and the blocklist ────────────────────────────────────────
def download_tag(req: Request) -> Response:
    from core.subscription_forms import clean_form, tag_download
    from core.subscriptions import DEFAULT_SITE_PRIORITY
    body = req.json()
    raw_sites = body.get("sites") if isinstance(body.get("sites"), list) else []
    form = {"query": body.get("query"), "sites": [{"site": str(x), "priority": DEFAULT_SITE_PRIORITY.get(str(x), 3)}
                                                  for x in raw_sites]}
    fields, error = clean_form(form)
    if error:
        return Response.json({"ok": False, "error": error}, 400)
    try:
        limit = max(1, min(TAG_LIMIT_MAX, int(body.get("limit") or 0)))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Сколько постов брать — число"}, 400)
    sub = tag_download(fields["query"], fields["sites"], limit)
    return _start(req, [sub], label=f"Скачать тег: {fields['query']}", kind="tag", forget_seeds=True)


def save_blocklist(req: Request) -> Response:
    from webui import settings_store
    from core.subscriptions import normalize_blacklist_tags
    text = " ".join(normalize_blacklist_tags(str(req.json().get("text") or "")))
    # both keys: the subscriptions read the old one when the new one is empty
    if not settings_store.save({"grabber_subscriptions_blocklist": text, "downloader_blocklist": text}):
        return Response.json({"ok": False, "error": "Программа не ответила — изменения не сохранены"}, 503)
    return Response.json({"ok": True, "tags": len(text.split())})


# ── «Удалить скачанные» ────────────────────────────────────────────────────
CLEAR_KIND = "subs_clear"


def sub_sessions(req: Request) -> Response:
    from core.subscription_cleanup import sessions, sharing
    from core.subscriptions import get_subscription
    sub = get_subscription(req.arg("id"))
    if sub is None:
        return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
    items = sessions(req.settings, sub)
    return Response.json({"ok": True, "query": str(sub.get("query") or sub.get("name") or ""), "sessions": items,
                          "files": sum(i["files"] for i in items), "bytes": sum(i["bytes"] for i in items),
                          "shared_with": sharing(sub)})


def clear_sessions(req: Request) -> Response:
    """The chosen sessions of a subscription to the trash, in a page task (a copy of the base comes first)."""
    from core import page_jobs
    from core.subscription_cleanup import CleanupError, busy_reason, chosen, trash_sessions
    from core.subscriptions import get_subscription
    from webui import data
    from webui.server import FILE_STREAMS
    from webui.settings_store import for_task
    body = req.json()
    sub = get_subscription(str(body.get("id") or ""))
    if sub is None:
        return Response.json({"ok": False, "error": "Такой подписки нет"}, 404)
    mode = str(body.get("mode") or "")
    if mode not in ("all", "keep_last", "pick"):
        return Response.json({"ok": False, "error": "Не выбрано, что удалить"}, 400)
    keys = [str(k) for k in body.get("keys") or [] if isinstance(k, str)] if mode == "pick" else []
    settings = for_task(req.settings)
    picked = chosen(settings, sub, keys=keys, mode=mode)
    if not picked or not sum(item["files"] for item in picked):
        return Response.json({"ok": False, "error": "В выбранном нет файлов"}, 400)
    why = busy_reason(str(sub.get("id") or ""))
    if why:
        return Response.json({"ok": False, "error": why}, 409)
    query = str(sub.get("query") or sub.get("name") or "")

    def run(progress, stop):
        try:
            result = trash_sessions(settings, sub, picked, reset_checkpoint=(mode == "all"),
                                    hold=FILE_STREAMS.holding, progress=progress, stop_check=stop)
        except CleanupError as exc:
            return {"failed": str(exc)}
        finally:
            data.TAG_COUNTS.invalidate()
        return result
    try:
        task = page_jobs.start(CLEAR_KIND, f"Удалить скачанные: {query}", run, about=str(sub.get("id") or ""))
    except page_jobs.TaskBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца."}, 409)
    return Response.json({"ok": True, "id": task.id})


def clear_stop(req: Request) -> Response:
    """«Остановить»: after the portion being moved; the files not reached stay where they are."""
    from core import page_jobs
    task = page_jobs.current(CLEAR_KIND)
    if task is None or not task.running:
        return Response.json({"ok": False, "error": "Перенос сейчас не идёт"}, 409)
    task.stop()
    return Response.json({"ok": True})


def clear_state(req: Request) -> Response:
    from core import page_jobs
    task = page_jobs.current(CLEAR_KIND)
    if task is None:
        return Response.json({"ok": True, "status": "idle"})
    return Response.json({"ok": True, **task.snapshot(after=req.int_arg("after", 0, lo=0))})
