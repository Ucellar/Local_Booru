"""The tab «Удаление и сброс» of «Диагностика» (/diag/reset): what the old settings page kept under «Удаление
данных» — the cards of a tag or a source, the parser's results, what the program downloaded (into the trash,
after a look at what a choice takes), the cards whose files are gone (out of the base) and «Удалить всё»
(done at the next start of the program: ``core/library_reset.py``).

A choice is shown first (``preview``: how many cards, how big, the first of them) and the page keeps the cards
it showed under a token; «В корзину» moves exactly those that still answer the choice (``core/library_cleanup``).
The moves and the cleanup are jobs of the diagnostics' lane (``maintenance.start_job``): one at a time with the
check and the maintenance, with the same journal, «Остановить» and history; while they go, the parser and the
subscriptions do not start (``quiet``)."""
from __future__ import annotations

import secrets
import threading
import time
from array import array

from webui.html import esc, human_size, page_frame, url
from webui.pages import diag, maintenance
from webui.server import Request, Response

PREVIEW_KEEP = 8                 # choices shown and not moved yet, kept for their «В корзину»
PREVIEW_TTL = 30 * 60            # seconds a shown choice stays valid
SAMPLE = 24                      # first cards of a choice shown as pictures
VALUE_LIMIT = 500                # a tag or an address longer than this is not a real one

_PREVIEWS: dict = {}
_PREVIEW_LOCK = threading.Lock()


def register(router) -> None:
    router.add("GET", r"/diag/reset", reset_page)
    router.add("POST", r"/api/reset/preview", preview)
    router.add("POST", r"/api/reset/trash", trash)
    router.add("POST", r"/api/reset/missing", missing)
    router.add("POST", r"/api/reset/full", full)
    router.add("POST", r"/api/reset/cancel", cancel)
    router.add("GET", r"/api/reset/hosts", hosts)


def _cards(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "карточка"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "карточки"
    return "карточек"


# ── a choice shown ─────────────────────────────────────────────────────────
def _keep(entry: dict) -> str:
    """Keep the cards a choice showed; returns the token its «В корзину» sends."""
    token = secrets.token_urlsafe(12)
    now = time.time()
    with _PREVIEW_LOCK:
        for key in [k for k, v in _PREVIEWS.items() if now - v["at"] > PREVIEW_TTL]:
            del _PREVIEWS[key]
        while len(_PREVIEWS) >= PREVIEW_KEEP:
            del _PREVIEWS[min(_PREVIEWS, key=lambda k: _PREVIEWS[k]["at"])]
        _PREVIEWS[token] = {**entry, "at": now}
    return token


def _shown(token: str) -> dict | None:
    with _PREVIEW_LOCK:
        entry = _PREVIEWS.get(token)
        if entry is not None and time.time() - entry["at"] > PREVIEW_TTL:
            del _PREVIEWS[token]
            entry = None
    return entry


def _choice(body: dict) -> tuple[str, str, str]:
    """(what, value, scope) of a request; ValueError with the words for the page when it is not a choice."""
    from core import library_cleanup as cleanup
    what = str(body.get("what") or "")
    value = str(body.get("value") or "").strip()
    scope = str(body.get("scope") or "all")
    if what not in cleanup.WHAT:
        raise ValueError("Нет такого выбора")
    if what in cleanup.MODES:
        if value not in dict(cleanup.MODES[what]):
            raise ValueError("Нет такого выбора")
        scope = "all"
    else:
        if not value:
            raise ValueError("Впишите тег" if what == "tag" else "Впишите сайт или адрес")
        if len(value) > VALUE_LIMIT:
            raise ValueError("Слишком длинно для тега или адреса")
        if scope not in dict(cleanup.SCOPES):
            raise ValueError("Нет такой части библиотеки")
    return what, value, scope


def _preview_html(what: str, value: str, scope: str, rows: list, token: str) -> str:
    from core import library_cleanup as cleanup
    label = cleanup.describe(what, value, scope)
    if not rows:
        return f'<p class="reset-none">Ничего не найдено: {esc(label)}.</p>'
    count = len(rows)
    size = sum(int(r.get("size_bytes") or 0) for r in rows)
    from webui import content_mode
    sfw = content_mode.mode() == "sfw"           # (v5.44.53) «SFW»: the gallery's pictures are not shown
    pictures = "".join(
        f'<li><a href="/post/{int(r["id"])}" title="{esc(r.get("file_name") or "")}">'
        f'<img src="/thumb/{int(r["id"])}" alt="{esc(r.get("file_name") or "")}" loading="lazy" decoding="async"></a></li>'
        for r in rows[:SAMPLE]) if not sfw else ""
    more = f'<p class="hint">…и ещё {diag._n(count - SAMPLE)}.</p>' if count > SAMPLE and not sfw else ""
    gallery = ""
    if what == "tag":
        gallery = (f'<a href="{esc(url("/posts", tags=cleanup.normalized("tag", value)))}">'
                   f'Открыть тег в галерее</a>')
    ask = f"Переместить {count} {_cards(count)} в корзину? Нажмите ещё раз"
    return (f'<p class="reset-count"><b>{diag._n(count)}</b> {_cards(count)}, {esc(human_size(size))} — {esc(label)}</p>'
            f'<ul class="reset-strip">{pictures}</ul>{more}'
            f'<div class="tool-actions"><button type="button" class="danger" data-action="reset-trash" '
            f'data-token="{esc(token)}" data-ask="{esc(ask)}" data-needs-idle{" disabled" if diag._busy() else ""}>'
            f'В корзину — {diag._n(count)}</button>{gallery}<span class="action-status" role="status"></span></div>')


def preview(req: Request) -> Response:
    from core import library_cleanup as cleanup
    try:
        what, value, scope = _choice(req.json())
        rows = cleanup.find_cards(req.settings, what, value, scope)
    except ValueError as exc:                    # CleanupError too
        return Response.json({"ok": False, "error": str(exc)}, 400)
    from core.database.connection import db_path
    shown = cleanup.shown_keys(rows)                         # 16 bytes a card: a look at 100 thousand stays small
    token = _keep({"what": what, "value": value, "scope": scope, "ids": array("q", shown.keys()),
                   "keys": array("q", shown.values()), "db": str(db_path(req.settings))}) if rows else ""
    return Response.json({"ok": True, "count": len(rows), "bytes": sum(int(r.get("size_bytes") or 0) for r in rows),
                          "token": token, "html": _preview_html(what, value, scope, rows, token)})


# ── the jobs ───────────────────────────────────────────────────────────────
def trash(req: Request) -> Response:
    from core import library_cleanup as cleanup
    token = str(req.json().get("token") or "")
    entry = _shown(token)
    from core.database.connection import db_path
    if entry is None or entry["db"] != str(db_path(req.settings)):     # card numbers belong to their base
        return Response.json({"ok": False, "error": "Показ устарел — нажмите «Показать» ещё раз"}, 409)
    what, value, scope = entry["what"], entry["value"], entry["scope"]
    shown = dict(zip(entry["ids"], entry["keys"]))

    def work(settings, progress, stop_check, options):
        from webui.server import FILE_STREAMS
        try:
            return cleanup.trash_cards(settings, shown, what=what, value=value, scope=scope, progress=progress,
                                       stop_check=stop_check, hold=lambda paths: FILE_STREAMS.holding(*paths))
        finally:
            maintenance._tags_changed()

    title = "В корзину: " + cleanup.describe(what, value, scope)
    job = maintenance.Job("reset_trash", title, title, "", work, quiet=True)
    from webui.settings_store import for_task
    answer = maintenance.start_job(job, for_task(req.settings), {})
    if answer.status == 200:
        with _PREVIEW_LOCK:                      # one press, one move: the next one needs a new look
            _PREVIEWS.pop(token, None)
    return answer


def _missing_work(settings, progress, stop_check, options):
    from core import library_cleanup as cleanup
    try:
        return cleanup.clear_missing_records(settings, progress=progress, stop_check=stop_check)
    finally:
        maintenance._tags_changed()


MISSING = maintenance.Job(
    "reset_missing", "Удалить записи без файлов", "Записи без файлов",
    "Убирает из базы карточки, чьих файлов больше нет на диске, — и те, что в корзине помечены «файл пропал». Диск "
    "или папка, которых сейчас нет, — не пропажа: их карточки остаются; если файлов нет у большей части "
    "библиотеки, не удаляется ничего. Карточки, которые убрали в корзину, не трогает — их убирает «Очистить "
    "корзину», и тогда эти картинки по-прежнему не вернутся. Сначала — копия базы.",
    _missing_work, ask="Убрать из базы карточки без файлов? Нажмите ещё раз", danger=True, quiet=True)


def missing(req: Request) -> Response:
    from webui.settings_store import for_task
    return maintenance.start_job(MISSING, for_task(req.settings), {})


def full(req: Request) -> Response:
    from core.library_reset import request_reset
    body = req.json()
    if str(body.get("confirm") or "").strip() != "DELETE":
        return Response.json({"ok": False, "error": "Впишите DELETE — так видно, что это не случайное нажатие"}, 400)
    request_reset(req.settings, include_subscriptions=bool(body.get("subscriptions")))
    return Response.json({"ok": True})


def cancel(req: Request) -> Response:
    from core.library_reset import cancel_reset, pending_reset, started
    if started(pending_reset()):
        return Response.json({"ok": False, "error": "Сброс уже начат — он доделается при следующем запуске"}, 409)
    if not cancel_reset():
        return Response.json({"ok": False, "error": "Сброс не заказан"}, 409)
    return Response.json({"ok": True})


def hosts(req: Request) -> Response:
    """The sites the library's cards come from, most cards first — the choices of «Источник»."""
    from core.database.connection import db
    with db(req.settings, readonly=True) as con:
        rows = con.execute(
            "SELECT LOWER(s.host) AS host, COUNT(DISTINCT isrc.image_id) AS cards FROM sources s "
            "JOIN image_sources isrc ON isrc.source_id=s.id JOIN images i ON i.id=isrc.image_id "
            "WHERE i.deleted=0 AND COALESCE(s.host,'')<>'' GROUP BY LOWER(s.host) ORDER BY cards DESC LIMIT 500").fetchall()
    items = []
    for r in rows:
        n = int(r["cards"] or 0)
        items.append({"host": r["host"], "cards": n, "label": f"{diag._n(n)} {_cards(n)}"})
    return Response.json({"ok": True, "items": items})


# ── the page ───────────────────────────────────────────────────────────────
def _options(pairs, counts: dict | None = None, current: str = "") -> str:
    out = []
    for value, label in pairs:
        text = label
        if counts is not None:
            n, size = counts.get(value, (0, 0))
            text += f" ({diag._n(n)}, {human_size(size)})" if n else " (пусто)"
        selected = " selected" if value == current else ""
        out.append(f'<option value="{esc(value)}"{selected}>{esc(text)}</option>')
    return "".join(out)


def _find_form(what: str, fields: str, button: str = "Показать") -> str:
    return (f'<form class="reset-find" data-form="reset-find" data-what="{esc(what)}">'
            f'<div class="reset-fields">{fields}<button type="submit">{esc(button)}</button>'
            f'<span class="action-status" role="status"></span></div>'
            f'<div class="reset-preview" data-preview aria-live="polite" hidden></div></form>')


def _block(block_id: str, title: str, lead: str, inner: str, *, cls: str = "") -> str:
    return (f'<section class="settings-block maint-group reset-block{cls}" id="{esc(block_id)}"><h2>{esc(title)}</h2>'
            f'<p class="hint">{lead}</p>{inner}</section>')


def _by_tag_html() -> str:
    from core import library_cleanup as cleanup
    fields = ('<label class="reset-field"><span>Что</span><select name="kind" aria-label="Что искать">'
              '<option value="tag">тег</option><option value="source">источник</option></select></label>'
              '<label class="reset-field reset-value" data-for="tag"><span>Тег</span>'
              '<input type="search" name="tag" autocomplete="off" spellcheck="false" data-autocomplete '
              'placeholder="например, sketch"></label>'
              '<label class="reset-field reset-value" data-for="source" hidden><span>Сайт или адрес</span>'
              '<input type="search" name="source" autocomplete="off" spellcheck="false" list="reset-hosts" disabled '
              'placeholder="например, danbooru.donmai.us"><datalist id="reset-hosts"></datalist></label>'
              f'<label class="reset-field"><span>Где</span><select name="scope">{_options(cleanup.SCOPES)}</select></label>')
    lead = ("Карточки с этим тегом — или с этим сайтом в источниках — уходят в корзину, оттуда их можно вернуть. "
            "Когда корзину очистят, те же картинки в библиотеку больше не попадут (запрет по MD5). "
            "Сайт — все его посты; полный адрес — один пост.")
    return _block("reset-find", "По тегу или источнику", esc(lead), _find_form("tag", fields))


def _results_html(counts: dict) -> str:
    from core import library_cleanup as cleanup
    parser = _find_form("parser", '<label class="reset-field"><span>Что</span><select name="mode">'
                        f'{_options(cleanup.PARSER_MODES, counts.get("parser"))}</select></label>')
    lead = ("Найденное парсером и Брак — копии картинок из исходной папки (сама она не меняется). В корзину, оттуда "
            "можно вернуть; пока они в корзине, парсер не добавит те же картинки снова.")
    downloads = _find_form("downloads", '<label class="reset-field"><span>Что</span><select name="mode">'
                           f'{_options(cleanup.DOWNLOAD_MODES, counts.get("downloads"))}</select></label>')
    lead_dl = ("Что скачала программа: граббер, подписки и расширение (папка subscriptions) и старый загрузчик "
               "(папка downloads). В корзину, оттуда можно вернуть; пока они в корзине, те же картинки не скачаются снова.")
    return (_block("reset-parser", "Результаты парсера", esc(lead), parser)
            + _block("reset-downloads", "Скачанное", esc(lead_dl), downloads))


def _missing_html(history: list, busy: bool) -> str:
    return _block("reset-missing", "Записи без файлов",
                  "Карточки, у которых пропал файл, — уже не вернуть: убрать их из базы, чтобы не мешали поиску и "
                  "проверке.",
                  maintenance._job_html(MISSING, history, busy).replace('data-form="maint"', 'data-form="reset-missing"', 1))


def _library_line(settings: dict) -> str:
    from core.database.connection import db
    try:
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT SUM(deleted=0), COALESCE(SUM(CASE WHEN deleted=0 THEN size_bytes END),0), "
                              "SUM(deleted<>0) FROM images").fetchone()
    except Exception as exc:                  # a broken base: the page still opens and can reset it
        maintenance._LOG.warning("reset page: cards not counted: %s: %s", type(exc).__name__, exc)
        return ""
    live, size, trashed = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    return (f'<p class="hint">Сейчас в библиотеке {diag._n(live)} {_cards(live)} ({esc(human_size(size))}), '
            f'в корзине — {diag._n(trashed)}.</p>')


def _result_line(result: dict | None) -> str:
    if not result:
        return ""
    when = esc(diag._when(result.get("at")))
    if result.get("done"):
        errors = result.get("errors") or []
        tail = f", ошибок: {len(errors)}" if errors else ""
        return (f'<p class="maint-last">Последний сброс {when}: удалено папок и файлов — {diag._n(result.get("removed"))}'
                f'{tail}; копия базы — {esc(result.get("backup") or "базы не было")}.</p>')
    return f'<p class="maint-last"><span class="tool-warn">Сброс не выполнен</span> {when}: {esc(result.get("error") or "")}</p>'


def _everything_html(settings: dict) -> str:
    from core.library_reset import SUBSCRIPTIONS_FOLDER, last_result, pending_reset, plan, started
    pending = pending_reset()
    what = plan(settings, include_subscriptions=True)
    items = []
    for path in what["remove"]:
        note = " — только с галочкой «и подписки»" if path.rstrip("\\/").endswith(SUBSCRIPTIONS_FOLDER) else ""
        items.append(f"<li><code>{esc(path)}</code>{esc(note)}</li>")
    items.append(f'<li><code>{esc(what["base"])}</code> — база: теги, источники, корзина, запреты «не добавлять '
                 f'снова», история скачивания</li>')
    items.append(f'<li><code>{esc(what["cache"])}</code> — кэш: превью, кэш граббера и парсера, список «Скрыть» '
                 f'граббера</li>')
    kept = "".join(f'<li><code>{esc(p)}</code> — <span class="tool-warn">останется: в ней исходная папка</span></li>'
                   for p in what["kept"])
    root = str(settings.get("root") or "")
    lead = ("Библиотека начинается заново: её папки с картинками (найденное, Брак, скачанное, корзина), база с тегами и "
            "источниками и кэш. " + (f"Исходная папка <code>{esc(root)}</code> не трогается. " if root else "")
            + "Копия базы остаётся в резервных копиях архива. Удаляется при следующем запуске программы (пока она "
            "работает, её файлы заняты) — перед этим программа спросит ещё раз.")
    if pending and started(pending):
        form = ('<div class="reset-ordered"><p><b>Сброс начат и не доделан</b> — программу закрыли посреди него. Он '
                'доделается при следующем запуске, без вопроса.</p></div>')
    elif pending:
        when = esc(diag._when(pending.get("at")))
        subs = " вместе с подписками" if pending.get("include_subscriptions") else " (подписки останутся)"
        form = (f'<div class="reset-ordered"><p><b>Сброс заказан</b>{subs} — {when}. Закройте программу и откройте снова: '
                'при запуске она спросит ещё раз и удалит всё.</p><div class="tool-actions">'
                '<button type="button" data-action="reset-cancel">Отменить сброс</button>'
                '<span class="action-status" role="status"></span></div></div>')
    else:
        form = ('<form class="reset-confirm" data-form="reset-full">'
                '<label class="reset-field"><span>Впишите DELETE</span><input type="text" name="confirm" '
                'autocomplete="off" spellcheck="false" placeholder="DELETE" aria-describedby="reset-all-lead"></label>'
                '<label class="check"><input type="checkbox" name="subscriptions" checked> и подписки — их список и папка '
                'subscriptions со всем, что скачали граббер, подписки и расширение (без галочки она останется, но без '
                'карточек)</label>'
                '<div class="tool-actions"><button type="submit" class="danger" disabled '
                'data-ask="Всё будет удалено при следующем запуске. Нажмите ещё раз">Удалить всё</button>'
                '<span class="action-status" role="status"></span></div></form>')
    inner = (f'{_library_line(settings)}<details><summary>Что будет удалено</summary>'
             f'<ul class="diag-files">{"".join(items)}{kept}</ul></details>{form}{_result_line(last_result())}')
    return (f'<section class="settings-block maint-group reset-block reset-all danger-zone" id="reset-all"><h2>Удалить всё</h2>'
            f'<p class="hint" id="reset-all-lead">{lead}</p>{inner}</section>')


def reset_page(req: Request) -> Response:
    from core import library_cleanup as cleanup
    from core.library_diagnostics import repair_history
    history = repair_history()
    busy = diag._busy()
    try:
        counts = cleanup.overview(req.settings)
    except Exception as exc:                  # a broken base: the choices without their numbers
        maintenance._LOG.warning("reset page: counts not read: %s: %s", type(exc).__name__, exc)
        counts = {}
    root = str(req.settings.get("root") or "")
    source = f" Исходная папка <code>{esc(root)}</code> не меняется никогда." if root else ""
    body = f"""
<section class="tool tool-wide settings diag maint reset" id="diag">
  <header class="tool-head">
    <h1>Удаление и сброс</h1>
    <p class="tool-lead">Удаление из рабочей библиотеки. Картинки уходят в корзину — их можно вернуть из «Корзины»
    галереи.{source} Перед каждым удалением программа делает копию базы.</p>
    <div class="tool-actions head-actions">
      <button type="button" class="danger" data-action="diag-stop"{"" if diag._can_stop() else " disabled"}>Остановить</button>
      <span class="action-status" role="status"></span>
    </div>
  </header>
  {diag._running_now()}
  <div class="job panel" data-job hidden>
    <div class="job-head"><b class="job-title"></b> <span class="job-status"></span></div>
    <pre class="job-log" tabindex="0" aria-label="Журнал удаления"></pre>
  </div>
  <div class="maint-groups">
    {_by_tag_html()}
    {_results_html(counts)}
    {_missing_html(history, busy)}
    {_everything_html(req.settings)}
  </div>
  {diag._history_html(history)}
</section>"""
    return Response.html(page_frame("Удаление и сброс", body, active="diag", subnav=False, kind="tool",
                                    body_data={"tool": "diag"}, tabs=diag.tabs("reset"), **diag._prefs()))
