"""Tool pages: the parser run (live log and progress), and the actions of the gallery sections
«Брак» (search again) and «Корзина» (restore, delete for good) on many cards at once."""
from __future__ import annotations

from pathlib import Path

from core.database.connection import db
from webui import data
from webui.html import esc, page_frame
from webui.server import Request, Response

RETRY_LIMIT = 5000          # Брак files one retry may take


def register(router) -> None:
    router.add("GET", r"/parser", parser_page)
    router.add("GET", r"/api/parser/state", parser_state)
    router.add("POST", r"/api/parser/start", parser_start)
    router.add("POST", r"/api/parser/(?P<what>pause|resume|stop)", parser_control)
    router.add("POST", r"/api/parser/site-again", parser_site_again)
    router.add("POST", r"/api/parser/folder", parser_folder)
    router.add("GET", r"/api/parser/thumb/(?P<job>\d+)/(?P<file>\d+)", parser_thumb)
    router.add("GET", r"/api/parser/file/(?P<job>\d+)/(?P<file>\d+)", parser_file)
    router.add("POST", r"/api/parser/open", parser_open)
    router.add("POST", r"/api/parser/retry", parser_retry)
    router.add("POST", r"/api/trash/restore", trash_restore)
    router.add("POST", r"/api/trash/purge", trash_purge)


def _prefs(req: Request) -> dict:
    from webui import prefs
    stored = prefs.load()
    return {"thumbs": stored.get("thumbs", "m")}         # the look is the page's own (page_frame)


# ── what the run will use, in words ────────────────────────────────────────
_REVERSE = (("enable_fluffle", "Fluffle", True), ("enable_e621_iqdb", "e621 IQDB", True),
            ("enable_atf_iqdb", "ATF IQDB", True), ("enable_iqdb", "IQDB", True),
            ("enable_danbooru_iqdb", "Danbooru IQDB", False), ("enable_ascii2d", "Ascii2D", False))


def site_plan(settings: dict) -> tuple[list, list]:
    """(sites asked by MD5, reverse-search services) as the parser will use them, by name."""
    from core.parser.sites import engine_class
    sites = []
    for domain, cfg in (settings.get("sites") or {}).items():
        if isinstance(cfg, dict) and cfg.get("enabled") and engine_class(cfg, domain) is not None:
            sites.append(str(cfg.get("name") or domain))
    for cfg in settings.get("custom_sites") or []:
        if isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("domain"):
            sites.append(str(cfg.get("name") or cfg.get("domain")))
    e621_on = bool(((settings.get("sites") or {}).get("e621.net") or {}).get("enabled"))
    atf_on = bool(((settings.get("sites") or {}).get("booru.allthefallen.moe") or {}).get("enabled"))
    # a site's own IQDB leads to that site's posts: without the site among the run's it is not asked (core/parser/reverse)
    reverse = [label for key, label, default in _REVERSE if settings.get(key, default)
               and (key != "enable_e621_iqdb" or e621_on) and (key != "enable_atf_iqdb" or atf_on)]
    if settings.get("enable_saucenao", True):
        reverse.append("SauceNAO" if str(settings.get("saucenao_api_key") or "").strip() else "SauceNAO (нет ключа — выключен)")
    return sites, reverse


# ── /parser ────────────────────────────────────────────────────────────────
def _chips(names: list, *, empty: str) -> str:
    if not names:
        return f'<span class="tool-warn">{esc(empty)}</span>'
    out = []
    for name in names:
        off = "нет ключа" in name
        label = name.split(" (", 1)[0] if off else name
        title = ' title="Нет ключа — выключен; ключ вписывается в «Настройки → Парсер и сайты»"' if off else ""
        out.append(f'<li class="plan-chip{" off" if off else ""}"{title}>{esc(label)}</li>')
    return f'<ul class="plan-chips">{"".join(out)}</ul>'


def manga_plan(settings: dict) -> list:
    """(v5.44.125) How the manga of the folder is confirmed (``core/parser_manga``), by name: [] — switched off."""
    if not settings.get("parser_skip_manga", True):
        return []
    out = ["ComicInfo.xml", "ссылка в файлах", "тайтл на сайтах манги — по имени и страницам"]
    if settings.get("enable_saucenao", True) and str(settings.get("saucenao_api_key") or "").strip():
        out.append("SauceNAO → сайт манги")
    return out


def _day(stamp: int) -> str:
    """The day of a moment in the words of the page: «сегодня», «вчера», «9.11»."""
    import datetime
    then = datetime.datetime.fromtimestamp(stamp).date()
    days = (datetime.date.today() - then).days
    return "сегодня" if days <= 0 else "вчера" if days == 1 else f"{then.day}.{then.month:02d}"


def _clock(stamp: int) -> str:
    import time
    return time.strftime("%H:%M", time.localtime(stamp))


def _ago(stamp: int) -> str:
    """When, in the words of the page: «сегодня в 06:22», «вчера в 23:10», «9.11 в 23:10» (tools.js says the same)."""
    return f"{_day(stamp)} в {_clock(stamp)}"


def _span(started: int, finished: int) -> str:
    """From when to when: «вчера, 23:10 — 06:22»; the end on another day — «вчера, 23:10 — сегодня, 06:22»."""
    end = _clock(finished) if _day(started) == _day(finished) else f"{_day(finished)}, {_clock(finished)}"
    return f"{_day(started)}, {_clock(started)} — {end}"


def _lasted(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes = round(seconds / 60)
    return f"{minutes} мин" if minutes < 60 else f"{minutes // 60} ч {minutes % 60} мин"


def last_words(note: dict | None) -> str:
    """(v5.44.160) «прошлый запуск — вчера в 23:10, шёл 7 ч 12 мин» (``core.parser.jobs.last_run``); "" — none yet."""
    if not note or not note.get("finished"):
        return ""
    words = f"прошлый запуск — {_ago(note['finished'])}"
    if note.get("started") and note["finished"] >= note["started"]:
        words += f", шёл {_lasted(note['finished'] - note['started'])}"
    if note.get("status") == "failed":
        words += " (остановлен ошибкой)"
    return words


COUNT_WORDS = (("tagged", "найдено"), ("nomatch", "в Брак"), ("deferred", "отложено"), ("skip", "пропущено"),
               ("error", "ошибки"))


def _sites_word(n: int) -> str:
    from webui.pages.post_sets import plural
    return plural(n, ("сайт", "сайта", "сайтов"))


def _services_word(n: int) -> str:
    from webui.pages.post_sets import plural
    return plural(n, ("сервис", "сервиса", "сервисов"))


def parser_page(req: Request) -> Response:
    """The page «Парсер» (v5.44.160: the mockup A — the owner: «парсер выглядит пиздец толи непонятно толи
    перегруженно»).  On top — what is going on: the run goes or not, how much is done, how much is left, who holds
    it; not running — the folder, one button and a line «что сделает».  Everything else — behind tabs: «Сейчас» (the
    files in work, each with what the sites have said of it; the last answers) or «Итог» of the last run, «Сайты»
    (one table of the queues), «Манга из папки» (``webui/pages/parser_manga.py``), «Журнал», «Параметры».  Every
    number and button of the page before is here — in another place.  A picture opens large in the viewer
    (tools.js).  The tab shown is in the address (``#sites``, ``#manga``, ``#log``, ``#params``)."""
    from core.parser import jobs
    from webui.pages import parser_manga
    s = req.settings
    root = str(s.get("root") or "")
    exists = bool(root) and Path(root).is_dir()
    sites, reverse = site_plan(s)
    manga = manga_plan(s)
    in_app = "QtWebEngine" in req.headers.get("User-Agent", "")
    if in_app:          # the window has a folder dialog; a browser page takes the path as text
        folder = (f'<code class="tool-path">{esc(root) or "не выбрана"}</code>'
                  '<button type="button" data-action="parser-choose-folder">Выбрать папку…</button>')
    else:
        folder = (f'<form class="inline-form" data-form="parser-folder"><input type="text" name="path" value="{esc(root)}" '
                  'spellcheck="false" aria-label="Папка с картинками" placeholder="Путь к папке">'
                  '<button type="submit">Сохранить</button></form>')
    folder_note = "" if exists else '<p class="tool-warn">Такой папки нет — выберите другую.</p>'
    recheck = " checked" if s.get("parser_recheck_new_sites") else ""
    nomatch_only = (" checked" if s.get("parser_recheck_nomatch_only") else "") + ("" if recheck else " disabled")
    legend = "".join(f'<li><i class="px-{key}"></i>{label} <b data-count="{key}">0</b></li>' for key, label in COUNT_WORDS)
    segments = "".join(f'<i class="seg seg-{key} px-{key}"></i>' for key, _label in COUNT_WORDS)
    working = [name for name in reverse if "нет ключа" not in name]
    plan = [f'<a href="#sites" data-px-go="sites">{_sites_word(len(sites))} по MD5</a>' if sites else
            '<a href="/sites" class="off">нет включённых сайтов</a>']
    if working:
        plan.append(f'<a href="#sites" data-px-go="sites">{_services_word(len(working))} обратного поиска</a>')
    plan.append('<a href="#manga" data-px-go="manga">манга из папки — в «Мангу»</a>' if manga else
                '<a href="#manga" data-px-go="manga" class="off">манга из папки — как все картинки</a>')
    plan_html = '<span class="arrow" aria-hidden="true">→</span>'.join(plan)
    last = jobs.last_run()
    said = last_words(last)
    manga_n = parser_manga.total(s)
    # the run going now: the page comes as it will be a moment later, when its script has asked (no jump of the top)
    job = jobs.current()
    live = job is not None and job.status in {"starting", "running", "paused", "stopping"}
    if job is not None and not live:                # the run of this start of the program: its result, not the note's
        last = None
        said = ""
    when_idle = " hidden" if live else ""
    state_word = {"starting": "Запускается", "running": "Идёт", "paused": "Пауза",
                  "stopping": "Останавливается"}.get(job.status if live else "", "Не запущен")
    state_cls = "" if live and job.status != "paused" else " paused" if live else " idle"

    def tab(key: str, words: str, n: str = "") -> str:
        on = key == "now"
        return (f'<button type="button" role="tab" id="px-tab-{key}" data-px-tab="{key}" aria-controls="px-{key}" '
                f'aria-selected="{"true" if on else "false"}"' + (' class="on"' if on else "")
                + f'><span data-px-tab-words>{words}</span>{n}</button>')
    tabs = (tab("now", "Сейчас" if live else "Итог")
            + tab("sites", "Сайты", f'<span class="px-n" data-px-n="sites">{len(sites) + len(working)}</span>')
            + tab("manga", "Манга из папки", f'<span class="px-n" data-px-n="manga"{"" if manga_n else " hidden"}>'
                                              f'{manga_n}</span>')
            + tab("log", "Журнал") + tab("params", "Параметры"))
    last_counts = (last or {}).get("counts") or {}
    last_when = ""
    if last and last.get("finished"):
        last_when = (_span(last["started"], last["finished"]) if last.get("started") else _ago(last["finished"]))
    body = f"""
<section class="tool px parser-tool" id="parser" data-state-url="/api/parser/state" data-status="{job.status if live else "idle"}">
  <header class="px-head">
    <h1>Парсер</h1>
    <p class="px-what">Ищет теги для картинок из папки: найденное — в галерею, ненайденное — в «Брак».</p>
  </header>
  <div class="px-status">
    <div class="px-row px-top">
      <span class="px-state{state_cls}"><i class="px-dot" aria-hidden="true"></i><b data-px-state>{state_word}</b></span>
      <span class="px-time" data-px-time>{"" if live else esc(said)}</span>
      <span class="px-actions tool-actions parser-buttons" role="group" aria-label="Запуск">
        <span class="action-status" role="status"></span>
        <button type="button" class="primary go" data-action="parser-start"{when_idle}>Запустить</button>
        <button type="button" data-action="parser-pause"{"" if live else " hidden"}>Пауза</button>
        <button type="button" class="danger" data-action="parser-stop"{"" if live else " hidden"}>Остановить</button>
      </span>
    </div>
    <div class="px-folder" data-px-when="idle"{when_idle}><span class="px-time">Папка</span>{folder}{folder_note}</div>
    <div class="px-plan" data-px-when="idle"{when_idle}><span>Что сделает:</span>{plan_html}</div>
    <div class="px-row" data-px-when="run" hidden>
      <span class="px-count"><b data-px-done>0</b> <small data-px-total></small></span>
      <span class="px-eta" data-px-eta></span>
    </div>
    <div class="px-bar run-bar" data-px-when="run" hidden role="progressbar" aria-label="Готово" aria-valuemin="0"
         aria-valuemax="1" aria-valuenow="0">{segments}<b class="run-percent"></b></div>
    <ul class="px-legend" data-px-when="run" hidden>{legend}</ul>
    <p class="px-hint" data-px-hint hidden></p>
    <div class="run-alert" role="status" hidden></div>
  </div>
  <nav class="px-tabs" role="tablist" aria-label="Что показать">{tabs}</nav>
  <div class="px-pane" id="px-now" role="tabpanel" aria-labelledby="px-tab-now" data-px-pane="now">
    <section class="px-last" data-px-last{"" if last and not live else " hidden"}>
      <h2>Прошлый запуск <small data-px-last-when>{esc(last_when)}</small></h2>
      <ul class="px-legend">{"".join(f'<li><i class="px-{key}"></i>{label} <b data-last="{key}">{int(last_counts.get(key) or 0)}</b></li>' for key, label in COUNT_WORDS)}</ul>
      <p class="px-last-said"><span data-px-last-said></span> <span class="job-links">Смотреть:
        <a href="/posts?section=found">найденные</a> · <a href="/posts?section=no_match">Брак</a></span></p>
    </section>
    <p class="parser-idle muted" data-idle{" hidden" if last or job is not None else ""}>Здесь будут картинки, которые
      парсер разбирает: нажмите «Запустить».</p>
    <section class="run-part run-flight" aria-labelledby="run-flight-title" data-job hidden>
      <h2 id="run-flight-title">В работе <small data-px-flight-n></small></h2>
      <ul class="px-cards flight-cards" aria-live="off"></ul>
      <p class="flight-more muted" hidden></p>
      <p class="flight-empty muted" hidden>Сейчас ни один файл не в работе.</p>
    </section>
    <section class="run-part run-recent" aria-labelledby="run-recent-title" hidden>
      <h2 id="run-recent-title">Только что <small data-px-recent-n></small></h2>
      <ul class="px-strip recent-strip"></ul>
    </section>
  </div>
  <div class="px-pane" id="px-sites" role="tabpanel" aria-labelledby="px-tab-sites" data-px-pane="sites" hidden>
    <div class="px-will" data-px-will>
      <dl class="parser-plan" aria-label="Где ищет">
        <div><dt>По MD5</dt><dd>{_chips(sites, empty="нет включённых сайтов")}</dd></div>
        <div><dt>Обратный поиск</dt><dd>{_chips(reverse, empty="выключен")}</dd></div>
      </dl>
      <p class="px-what">Сначала файл спрашивают сайты по точному MD5, потом — сервисы обратного поиска, по похожей
        картинке. Какие сайты спрашивать и ключи к ним — <a href="/sites">Настройки → Парсер и сайты</a>. Когда парсер
        идёт, здесь таблица: кто сколько ждёт и кто дольше всех.</p>
    </div>
    <div class="run-queues" data-px-queues hidden>
      <table class="px-table">
        <thead><tr><th scope="col">Кто</th><th scope="col" class="q-wide">Как спрашивается</th>
          <th scope="col" class="n">Ждут</th><th scope="col" class="n q-rest">В минуту</th>
          <th scope="col" class="n">Осталось</th><th scope="col" class="q-rest">Сделано</th></tr></thead>
        <tbody class="site-queues" data-kind="files" hidden>
          <tr class="group queues-group" data-for="files"><th scope="rowgroup" colspan="6">Файлы — чтение и запись в библиотеку</th></tr>
        </tbody>
        <tbody class="site-queues" data-kind="site">
          <tr class="group queues-group"><th scope="rowgroup" colspan="6">Сайты — точный MD5</th></tr>
        </tbody>
        <tbody class="site-queues" data-kind="reverse" hidden>
          <tr class="group queues-group" data-for="reverse"><th scope="rowgroup" colspan="6">Обратный поиск — похожие картинки, для тех, кого нет по MD5</th></tr>
        </tbody>
      </table>
      <p class="px-what queues-lead">«Осталось» — вместе с файлами, которые ещё не прочитаны. У каждого сайта и
        сервиса своя очередь: медленный не держит остальных, найденное сразу записывается в библиотеку. Сайт
        отключился — его строка станет красной, а наверху появится «Проверить снова».</p>
    </div>
  </div>
  <div class="px-pane mx" id="px-manga" role="tabpanel" aria-labelledby="px-tab-manga" data-px-pane="manga" hidden>
    {parser_manga.block(s)}
  </div>
  <div class="px-pane" id="px-log" role="tabpanel" aria-labelledby="px-tab-log" data-px-pane="log" hidden>
    <div class="run-part run-log">
      <div class="log-tabs" role="tablist" aria-label="Что показать в журнале"></div>
      <pre class="job-log" tabindex="0" aria-label="Журнал запуска"></pre>
      <p class="px-what" data-px-nolog>Журнал появится, когда парсер запустится.</p>
    </div>
  </div>
  <div class="px-pane" id="px-params" role="tabpanel" aria-labelledby="px-tab-params" data-px-pane="params" hidden>
    <section class="px-params">
      <h2>Что делать с файлами, которые парсер уже видел</h2>
      <label class="check parser-recheck"><input type="checkbox" name="recheck"{recheck}> <span><b>Добор новых сайтов</b>
        <small>обработанные раньше файлы ищутся только там, где их ещё не искали; сначала — новые файлы, потом
        Брак, потом найденные</small></span></label>
      <label class="check parser-recheck parser-recheck-only"><input type="checkbox" name="recheck_only"{nomatch_only}>
        <span><b>Добор только для Брака</b> <small>уже найденным файлам теги других сайтов не добираются — так
        быстрее</small></span></label>
      <p class="px-what">Галочки берёт следующий запуск.</p>
    </section>
    <section class="px-params">
      <h2>Где ищет</h2>
      <dl class="parser-plan" aria-label="Где ищет">
        <div><dt>По MD5</dt><dd>{_chips(sites, empty="нет включённых сайтов")}</dd></div>
        <div><dt>Обратный поиск</dt><dd>{_chips(reverse, empty="выключен")}</dd></div>
        <div><dt>Манга</dt><dd>{_chips(manga, empty="выключено — все папки как обычно")}</dd></div>
        <div class="plan-edit"><dt></dt><dd><a href="/sites">Настройки → Парсер и сайты</a></dd></div>
      </dl>
    </section>
  </div>
  <dialog class="parser-viewer" aria-labelledby="pv-title">
    <div class="pv-media">
      <img alt="" hidden>
      <video controls preload="metadata" hidden></video>
      <button type="button" class="pv-nav pv-prev" data-action="pv-prev" aria-label="Предыдущая">‹</button>
      <button type="button" class="pv-nav pv-next" data-action="pv-next" aria-label="Следующая">›</button>
    </div>
    <div class="pv-side">
      <h2 id="pv-title"></h2>
      <p class="pv-what"></p>
      <ul class="pv-chips"></ul>
      <div class="tool-actions pv-actions">
        <a class="button primary" data-pv-card hidden>Открыть карточку</a>
        <button type="button" data-action="pv-open">Открыть файл</button>
        <button type="button" data-action="pv-reveal">Показать в папке</button>
        <span class="action-status" role="status"></span>
      </div>
      <button type="button" class="pv-close" data-action="pv-close">Закрыть</button>
    </div>
  </dialog>
</section>"""
    return Response.html(page_frame("Парсер", body, active="parser", subnav=False, kind="tool",
                                    body_data={"tool": "parser"}, **_prefs(req)))


# ── the parser API ─────────────────────────────────────────────────────────
def parser_state(req: Request) -> Response:
    from core.parser import jobs
    job = jobs.current()
    if job is None:
        busy = jobs.owner()
        return Response.json({"ok": True, "status": "busy" if busy else "idle", "owner": busy,
                              "last": jobs.last_run()})         # (v5.44.160) the run before this start of the program
    snap = job.snapshot(after=req.int_arg("after", 0, lo=0))
    snap["ok"] = True
    return Response.json(snap)


def _busy(exc) -> Response:
    return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или остановите."}, 409)


def parser_start(req: Request) -> Response:
    from core.parser import jobs
    s = dict(req.settings)
    root = str(s.get("root") or "")
    if not root or not Path(root).is_dir():
        return Response.json({"ok": False, "error": "Папка с картинками не выбрана или её нет"}, 400)
    body = req.json()
    changes = {key: bool(body.get(field)) for field, key in (("recheck_new_sites", "parser_recheck_new_sites"),
                                                               ("recheck_nomatch_only", "parser_recheck_nomatch_only"))
               if field in body}
    if changes:
        s.update(changes)
        _save_settings(changes)
    s.pop("_parser_explicit_paths", None)
    try:
        job = jobs.start(s, label="Парсер: " + Path(root).name, kind="run")
    except jobs.ParserBusy as exc:
        return _busy(exc)
    return Response.json({"ok": True, "id": job.id})


def parser_site_again(req: Request) -> Response:
    """«Проверить снова» for a site switched off in the run going now (its cookies renewed, its key written)."""
    from core.parser import jobs
    host = str(req.json().get("host") or "").strip()
    job = jobs.current()
    if not host:
        return Response.json({"ok": False, "error": "Не указан сайт"}, 400)
    if job is None:
        return Response.json({"ok": False, "error": "Парсер сейчас не работает"}, 409)
    why = job.site_again(host)
    if why:
        return Response.json({"ok": False, "error": why}, 409)
    return Response.json({"ok": True})


def parser_control(req: Request) -> Response:
    from core.parser import jobs
    job = jobs.current()
    if job is None or job.status not in {"running", "paused", "starting"}:
        return Response.json({"ok": False, "error": "Парсер сейчас не работает"}, 409)
    getattr(job, req.params["what"])()
    return Response.json({"ok": True, "status": job.status})


# ── the pictures of a run: by their number in it (core/parser/jobs.ParserJob.path_of), never by a path ──
THUMB = 360            # a file in work, as its card shows it
FRAME = 1600           # a video's frame in the viewer


def _run_file(job_id, number) -> tuple[Path | None, str]:
    """(the file, "") of the run shown now (the current or the last one), or (None, why not)."""
    from core.parser import jobs
    job = jobs.current()
    try:
        job_id, number = int(job_id), int(number)
    except (TypeError, ValueError):
        return None, "Нет такого файла"
    if job is None or job.id != job_id:
        return None, "Этот запуск уже не показывается"
    path = job.path_of(number)
    if path is None:
        return None, "Этого файла уже нет в списке запуска"
    if not path.is_file():
        return None, "Файла уже нет на диске"
    return path, ""


def parser_thumb(req: Request) -> Response:
    from webui.pages.media import _MAKING, _placeholder
    path, _why = _run_file(req.params["job"], req.params["file"])
    if path is None:
        return _placeholder("нет файла")
    from core.image_safe import safe_thumbnail_path
    with _MAKING:
        made = safe_thumbnail_path(str(path), THUMB, THUMB)
    if not made or not Path(made).is_file():
        return _placeholder("нет превью")
    return Response(200, content_type="image/jpeg", file=Path(made), headers={"Cache-Control": "private, max-age=600"})


def parser_file(req: Request) -> Response:
    """The file itself (a picture, a video — streamed), or with ``frame=1`` a large frame of it."""
    import mimetypes
    from webui.pages.media import _MAKING, _placeholder
    path, why = _run_file(req.params["job"], req.params["file"])
    if path is None:
        return Response.not_found(why)
    if req.arg("frame"):
        from core.image_safe import safe_thumbnail_path
        with _MAKING:
            made = safe_thumbnail_path(str(path), FRAME, FRAME)
        if not made or not Path(made).is_file():
            return _placeholder("нет кадра")
        return Response(200, content_type="image/jpeg", file=Path(made), headers={"Cache-Control": "private, max-age=600"})
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return Response(200, content_type=ctype, file=path, headers={"Cache-Control": "private, max-age=600"})


def parser_open(req: Request) -> Response:
    """«Открыть файл» / «Показать в папке» of the viewer: the source file in its usual program, like the old
    parser page's double click on a preview."""
    from webui import desktop
    body = req.json()
    path, why = _run_file(body.get("job"), body.get("file"))
    if path is None:
        return Response.json({"ok": False, "error": why}, 404)
    try:
        (desktop.reveal if body.get("reveal") else desktop.open_file)(path)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не удалось открыть: {exc}"}, 500)
    return Response.json({"ok": True})


def _save_settings(changes: dict) -> None:
    from webui import settings_store
    settings_store.save(changes)


def parser_folder(req: Request) -> Response:
    path = str(req.json().get("path") or "").strip().strip('"')
    if not path or not Path(path).is_dir():
        return Response.json({"ok": False, "error": "Такой папки нет"}, 400)
    _save_settings({"root": str(Path(path))})
    return Response.json({"ok": True, "root": str(Path(path))})


# ── many cards at once ─────────────────────────────────────────────────────
def _ids(body: dict) -> list:
    raw = body.get("ids")
    if not isinstance(raw, list):
        return []
    out = []
    for value in raw[:RETRY_LIMIT]:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _rows(settings: dict, ids: list, where: str = "1=1") -> list:
    if not ids:
        return []
    out = []
    with db(settings, readonly=True) as con:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            ph = ",".join("?" for _ in chunk)
            out += [dict(r) for r in con.execute(
                f"SELECT id, path, deleted, lifecycle, bucket FROM images WHERE id IN ({ph}) AND {where}", chunk)]
    return out


def retry_button(ctx: dict, total: int) -> str:
    """(v5.44.116) «Искать снова» of the whole section «Брак» (its select bar): the whole Брак, or — a search or a
    filter in the address (``брак:фото``…) — exactly the cards found, as the trash's «Вернуть все найденные (N)»
    (the review: «Искать снова весь Брак» sent the whole Брак whatever filter was chosen)."""
    narrowed = bool(ctx["tags"]) or ctx["source"] != "all"
    text = f"Искать снова найденные ({total})" if narrowed else "Искать снова весь Брак"
    return (f'<button type="button" data-action="nomatch-retry-all" data-tags="{esc(ctx["tags"])}" '
            f'data-source="{esc(ctx["source"])}" data-count="{total}" '
            f'title="Ещё раз через парсер {"все карточки Брака этого поиска" if narrowed else "весь Брак"}: сайты по '
            f'MD5 и обратный поиск">{esc(text)}</button>')


def _retry_said(queued: int, skipped: int, left: int) -> str:
    """(v5.44.116) What «Искать снова» of the whole Брак (or of the cards found) says when it has started."""
    from webui.pages.post_sets import FILES, plural
    text = f"Ищу снова: {plural(queued, FILES)}"
    if skipped:
        text += f" (ещё {plural(skipped, FILES)} нет на диске)"
    if left:
        text += (f"; остальные {left} ждут — нажмите «Искать снова» ещё раз, когда этот поиск закончится "
                 f"(за раз — не больше {RETRY_LIMIT})")
    return text


def parser_retry(req: Request) -> Response:
    """«Брак → Искать снова» for the chosen cards (or the whole Брак, or (v5.44.116) the Брак cards of the search
    ``tags`` / ``source`` of the page — exactly those): a parser run over them."""
    from core import nomatch_retry
    from core.nomatch_db import list_nomatches
    from core.parser import jobs
    body = req.json()
    s = dict(req.settings)
    left = 0
    if body.get("all"):
        tags = str(body.get("tags") or "").strip()
        source = str(body.get("source") or "all").strip() or "all"
        if tags or source != "all":           # (v5.44.116) the cards found by the page's search, as the trash does
            ids = data.result_ids(s, tags, section="no_match", source=source)
            rows = _rows(s, ids, "bucket='no_match' AND deleted=0")
            found = nomatch_retry.items_for_media(s, [r["path"] for r in rows])
            label, none = "найденные в Браке", "В Браке нет таких карточек"
        else:
            found = list_nomatches(None, settings=s)
            label, none = "весь Брак", "Брак пуст"
        if not found:
            return Response.json({"ok": False, "error": none}, 400)
        # (v5.44.116) more than one run takes: the ones waiting longest first — a file searched again and still in
        # Брак is written anew (its time is now), so the next press takes the rest instead of the same files again
        found.sort(key=lambda item: float(item.get("ts") or 0))
        items = found[:RETRY_LIMIT]
        left = len(found) - len(items)
        label += f" ({len(items)} из {len(found)})" if left else f" ({len(items)})"
    else:
        ids = _ids(body)
        if not ids:
            return Response.json({"ok": False, "error": "Не выбрано ни одной карточки"}, 400)
        rows = _rows(s, ids, "bucket='no_match' AND deleted=0")
        items = nomatch_retry.items_for_media(s, [r["path"] for r in rows])
        label = f"выбранные карточки Брака ({len(items)})"
    if not items:
        return Response.json({"ok": False, "error": "Среди выбранных нет файлов из Брака"}, 400)
    if jobs.is_running():
        return Response.json({"ok": False, "error": jobs.busy_text(jobs.owner())}, 409)
    nomatch_retry.restore_missing_copies(s, items)
    queue_root, queued, skipped = nomatch_retry.prepare_queue(s, items, label=label)
    if not queue_root or queued <= 0:
        return Response.json({"ok": False, "error": "Файлов выбранных карточек нет на диске"}, 409)
    run = nomatch_retry.retry_settings(s, queue_root, label=label, count=queued)
    try:
        job = jobs.start(run, label=f"Повторный поиск: {label}", kind="retry")
    except jobs.ParserBusy as exc:
        return _busy(exc)
    return Response.json({"ok": True, "id": job.id, "queued": queued, "skipped": len(skipped), "left": left,
                          "message": _retry_said(queued, len(skipped), left)})


def trash_restore(req: Request) -> Response:
    from core.library_lifecycle import restore_from_trash
    from webui.server import FILE_STREAMS
    ids = _ids(req.json())
    rows = _rows(req.settings, ids, "lifecycle IN ('trash','missing')")
    if not rows:
        return Response.json({"ok": False, "error": "Среди выбранных нет карточек из корзины"}, 400)
    with FILE_STREAMS.holding(*[r["path"] for r in rows]):
        result = restore_from_trash(req.settings, [r["id"] for r in rows])
    data.TAG_COUNTS.invalidate()
    done = int(result.get("restored") or 0) + int(result.get("merged_existing") or 0)
    return Response.json({"ok": bool(done), "restored": int(result.get("restored") or 0),
                          "merged": int(result.get("merged_existing") or 0), "errors": int(result.get("errors") or 0),
                          **({} if done else {"error": "Не удалось вернуть: файлов нет ни в корзине, ни на месте"})})


def trash_purge(req: Request) -> Response:
    """Delete for good: the chosen trashed cards, or the whole trash (``all``).  An empty list is
    refused — ``purge_trash`` takes «no ids» as «everything»."""
    from core.library_lifecycle import purge_trash
    from core.trash_tools import restoring
    from webui.server import FILE_STREAMS
    body = req.json()
    if restoring():             # it would take the cards «Вернуть всё» has not reached yet
        return Response.json({"ok": False, "error": "Идёт «Вернуть всё» — дождитесь конца или остановите его"}, 409)
    if body.get("all"):
        with db(req.settings, readonly=True) as con:
            ids = [int(r[0]) for r in con.execute(
                "SELECT id FROM images WHERE lifecycle IN ('trash','missing') AND deleted=1")]
    else:
        ids = _ids(body)
    rows = _rows(req.settings, ids, "lifecycle IN ('trash','missing') AND deleted=1")
    if not rows:
        return Response.json({"ok": False, "error": "Среди выбранных нет карточек из корзины"}, 400)
    with FILE_STREAMS.holding(*[r["path"] for r in rows]):
        result = purge_trash(req.settings, [r["id"] for r in rows], make_backup=True)
    data.TAG_COUNTS.invalidate()
    if result.get("error"):
        return Response.json({"ok": False, "error": result["error"]}, 409)
    return Response.json({"ok": True, "removed": int(result.get("removed_records") or 0),
                          "files": int(result.get("removed_files") or 0), "errors": int(result.get("errors") or 0)})
