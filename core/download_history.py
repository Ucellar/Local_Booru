"""(v5.44.120) «Загрузки → Готово» — what each download brought and what it did not, and why, for 7 days.

The owner approved the plan's mockup C («Норм»): one «Загрузки» for everything that downloads, and under it «Готово» —
«Пришли 2 главы: 40 и 41», «Скачано 10 глав из 12. Не скачались 2: Глава 51 — сайт убрал главу (ответ 404)…»,
«Остановлено: 40 из 120», with «Повторить не скачавшееся (N)» and «Открыть».

Every download is noted at its end (:func:`install` — once a run of the program):

* manga and manhwa of the sites — each title as its download ends (``manhwa_download.ENDED``: the nightly round of new
  chapters too, one line a title, «ночная проверка»); the chapters that did not come, and the ones a stop left, with
  their ids (``manhwa_run``) — «Повторить не скачавшееся» puts exactly them in the queue; a download that failed
  before its chapters (its site did not answer) — by its page task's end;
* nhentai — by its page task's end (``manga_get.GET``): the galleries that did not come;
* the videos — the other chat's: by the end of its page task (``page_jobs.on_end``), its result and its series' folder
  only read, not a line of its code changed;
* «Галерея (booru)» and the subscriptions — by the end of their check (``subscription_jobs.ENDED``).

Kept in ``download_history.json`` in the settings folder — written whole (``.tmp`` → ``os.replace``, the last good copy
``.bak``) under a lock; what is older than :data:`KEEP_DAYS` goes at every read and write.  Nothing of a key, a cookie
or a path of the owner's goes there but the folders of the library (the pages never get them: «Открыть» asks the
program by the line's number).  Whether a line is hidden by «SFW» is decided when it is shown (a title marked «Это
хентай» later is hidden too) — ``webui/pages/downloads.py``.
"""
from __future__ import annotations

import itertools
import json
import logging
import re
import threading
import time
from pathlib import Path

_LOG = logging.getLogger("local_booru.downloads")

KEEP_DAYS = 7
KEEP_SECONDS = KEEP_DAYS * 24 * 3600
NAME = "download_history.json"
MAX_ENTRIES = 600                 # lines kept at most (the newest)
MAX_FAILS = 300                   # what did not come, kept per line
VIDEO_TASK = "video_download"     # ``webui/pages/video_get.GET`` — the other chat's page task (only its end is read)
MANGA_TASK = "manga_download"     # ``webui/pages/manga_get.GET`` — nhentai
CHAPTERS = ("глава", "главы", "глав")
GALLERIES = ("галерея", "галереи", "галерей")
FILES = ("файл", "файла", "файлов")
PICTURES = ("картинка", "картинки", "картинок")
SUBS = ("подписка", "подписки", "подписок")
TITLES = ("тайтл", "тайтла", "тайтлов")
LEFT = "не дошла очередь — скачивание остановили"

_LOCK = threading.Lock()
_IDS = itertools.count(1)
_INSTALLED = threading.Event()
_ROUND: dict = {}                 # the nightly round's last end (in memory): «Сама программа» says it


def agree(n: int, forms: tuple) -> str:
    """The word that goes with the number ``n`` — («Скачана», «Скачаны», «Скачано»): «Скачана 1 глава», «Скачаны 2
    главы», «Скачано 10 глав»."""
    d, h = int(n) % 10, int(n) % 100
    return forms[0] if d == 1 and h != 11 else forms[1] if 2 <= d <= 4 and not 12 <= h <= 14 else forms[2]


def plural(n: int, forms: tuple) -> str:
    return f"{n} {agree(n, forms)}"


GOT = ("Скачана", "Скачаны", "Скачано")            # a chapter, a gallery, a picture
GOT_FILES = ("Скачан", "Скачаны", "Скачано")       # a file
NOT_GOT = ("Не скачалась", "Не скачались", "Не скачались")
NOT_GOT_FILES = ("не скачался", "не скачались", "не скачались")
CAME = ("Пришла", "Пришли", "Пришли")
CHECKED = ("Проверена", "Проверены", "Проверено")   # a subscription
CHECKED_TITLES = ("Проверен", "Проверены", "Проверено")


def _listed(numbers: list) -> str:
    """«40 и 41», «40, 41 и 42»."""
    return numbers[0] if len(numbers) == 1 else ", ".join(numbers[:-1]) + " и " + numbers[-1]


def _clean(text) -> str:
    from core.redaction import sanitize_text
    return sanitize_text(str(text or "")).strip()[:300]


# ── the file ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / NAME


def _fresh(items: list, now: float) -> list:
    return [x for x in items if isinstance(x, dict) and now - float(x.get("at") or 0) <= KEEP_SECONDS]


def _read() -> list:
    from core.file_safety import read_json_kept
    try:
        data = read_json_kept(_file(), dict, what="download history") or {}
    except OSError as exc:
        _LOG.warning("the history of downloads cannot be read now: %s", exc)
        return []
    items = data.get("entries") if isinstance(data.get("entries"), list) else []
    return [x for x in items if isinstance(x, dict) and x.get("id")]


def entries(now: float | None = None) -> list:
    """The lines of the last :data:`KEEP_DAYS` days, the newest first."""
    now = time.time() if now is None else now
    with _LOCK:
        items = _fresh(_read(), now)
    return sorted(items, key=lambda x: float(x.get("at") or 0), reverse=True)


def get(entry_id: str) -> dict | None:
    return next((x for x in entries() if str(x.get("id")) == str(entry_id)), None)


def revision() -> int:
    """Changes when a line is added (the page asks for «Готово» again then): the file's time."""
    try:
        return int(_file().stat().st_mtime_ns)
    except OSError:
        return 0


def add(entry: dict) -> dict:
    """A line noted (``at`` — now, unless the entry says when) and the lines older than :data:`KEEP_DAYS` let go — the
    whole file written anew under the lock."""
    from core.file_safety import write_json_kept
    now = time.time()
    line = dict(entry)
    line.setdefault("at", now)
    line["id"] = f"{int(float(line['at']) * 1000)}-{next(_IDS)}"
    line["fails"] = list(line.get("fails") or [])[:MAX_FAILS]
    with _LOCK:
        items = _fresh(_read() + [line], now)       # the lines of more than 7 days ago go (this one too, if it is)
        items = sorted(items, key=lambda x: float(x.get("at") or 0))[-MAX_ENTRIES:]
        write_json_kept(_file(), json.dumps({"entries": items}, ensure_ascii=False), dict)
    return line


def last_round() -> dict:
    """The nightly round's last end in this run of the program: {"at", "titles", "got", "problems", "stopped"}."""
    with _LOCK:
        return dict(_ROUND)


# ── what ended ─────────────────────────────────────────────────────────────────────────────────────────────────────
def install() -> None:
    """The ends of downloads noted from now on (once a run of the program)."""
    if _INSTALLED.is_set():
        return
    _INSTALLED.set()
    from core import manhwa_download, page_jobs, subscription_jobs
    if _manhwa_ended not in manhwa_download.ENDED:
        manhwa_download.ENDED.append(_manhwa_ended)
    if _subs_ended not in subscription_jobs.ENDED:
        subscription_jobs.ENDED.append(_subs_ended)
    for kind in (manhwa_download.TASK, MANGA_TASK, VIDEO_TASK):
        page_jobs.on_end(kind, _task_ended)


def _night() -> bool:
    """The download going is one of the nightly round's titles."""
    from core import background_duties, manhwa_download, page_jobs
    task = page_jobs.current(manhwa_download.TASK)
    return task is not None and task.running and task.label == background_duties.ROUND_LABEL


def _short(name: str) -> str:
    """«Том 1 Глава 40 - Возвращение» → «40» for a line of «Готово» («Пришли 2 главы: 40 и 41»)."""
    found = re.search(r"Глава\s+([\d.]+)", str(name or ""))
    return found.group(1) if found else str(name or "")[:40]


def _manhwa_ended(found, result: dict) -> None:
    """A title of the sites downloaded (or a stop): what came, what did not (every chapter not «done» of the run —
    the ones that failed with why, the ones a stop left), the chapters to ask again."""
    from core import manhwa_download as D, manhwa_run as R, manhwa_sources as S
    _meter, items = R.meter(since=0, run=-1)
    came = int(result.get("got") or 0)
    why = {str(x.get("id")): x for x in result.get("failed_chapters") or [] if isinstance(x, dict)}
    left = "отложена: сайт перестал отдавать главы — скачается в следующий раз" if result.get("gave_up") else LEFT
    fails, ids = [], []
    for item in items:
        if item.get("state") == "done":
            continue
        cid = str(item.get("id") or "")
        told = why.get(cid) or {}
        fails.append({"label": str(item.get("label") or ""), "why": _clean(told.get("why") or item.get("why") or left)})
        if cid:
            ids.append(cid)
    for cid, told in why.items():                     # (locked ones — never begun: not among the run's items)
        if cid not in ids:
            fails.append({"label": str(told.get("label") or ""), "why": _clean(told.get("why"))})
            ids.append(cid)
    stopped = bool(result.get("stopped"))
    wanted = came + len(fails)
    names = [_short(n) for n in result.get("chapters") or []]
    if stopped:
        said, sign = f"Остановлено: {agree(came, GOT).lower()} {plural(came, CHAPTERS)} из {wanted}.", "stop"
    elif fails and came:
        said, sign = (f"{agree(came, GOT)} {plural(came, CHAPTERS)} из {wanted}. "
                      f"{agree(len(fails), NOT_GOT)} {len(fails)}:"), "part"
    elif fails:
        said, sign = f"Не скачалось ни одной главы из {wanted}:", "fail"
    elif came:
        shown = _listed(names[:4]) + (" …" if len(names) > 4 else "") if names else ""
        said, sign = f"{agree(came, CAME)} {plural(came, CHAPTERS)}" + (f": {shown}." if shown else "."), "ok"
    else:
        said, sign = "Новых глав нет — всё уже в папке.", "ok"
    if result.get("gave_up"):
        said += " " + _clean(result["gave_up"])
    if result.get("helped"):
        said += f" С других сайтов: {plural(int(result['helped']), CHAPTERS)}."
    site = found.title.site
    adult = bool(getattr(S.by_name(site), "adult_only", False))
    from types import SimpleNamespace
    from core import content_marks
    hentai = adult or bool(content_marks.manhwa_reason(SimpleNamespace(info=found.title.info())))
    folder = str(result.get("folder") or found.folder)
    retry = None
    if ids:
        source = D.read_source(Path(folder)) if folder else {}
        retry = {"url": found.title.url, "branch": found.branch.id if found.branch else "",
                 "where": str(result.get("kind") or found.kind), "follow": bool(source.get("follow", True)),
                 "helpers": bool(found.helpers_on), "chapters": ids, "title": found.title.title}
    add({"kind": "manhwa", "what": "Хентай" if adult else ("Манга" if result.get("kind") == "manga" else "Манхва"),
         "title": found.title.title, "night": _night(), "sign": sign, "said": said, "fails": fails, "retry": retry,
         "hentai": hentai, "site": site, "folder": folder, "folder_kind": str(result.get("kind") or found.kind)})


def _task_ended(task) -> None:
    from core import background_duties, manhwa_download
    result = task.result if isinstance(task.result, dict) else {}
    if task.kind == manhwa_download.TASK:
        if task.label == background_duties.ROUND_LABEL:
            _round_ended(task, result)
        elif task.status == "failed":              # its chapters never began: the site, the title (said in words)
            title = re.sub(r"^Скачать:\s*", "", str(task.label or ""))
            add({"kind": "manhwa", "what": "Манхва", "title": title, "sign": "fail",
                 "said": "Не скачалось: " + _clean(task.error or result.get("failed") or "причина в журнале"),
                 "fails": [], "retry": None, "hentai": False, "site": "", "folder": ""})
    elif task.kind == MANGA_TASK:
        _galleries_ended(task, result)
    elif task.kind == VIDEO_TASK:
        _video_ended(task, result)


def _round_ended(task, result: dict) -> None:
    """The nightly round: its sum for «Сама программа»; a line of «Готово» when sites did not answer (the titles it
    downloaded have their own lines)."""
    problems = [_clean(p) for p in result.get("problems") or []]
    with _LOCK:
        _ROUND.update(at=time.time(), titles=int(result.get("titles") or 0), got=int(result.get("got") or 0),
                      problems=len(problems), stopped=bool(result.get("stopped")) or task.status == "stopped",
                      failed=_clean(task.error) if task.status == "failed" else "")
    if problems:
        titles = int(result.get("titles") or 0)
        add({"kind": "manhwa", "what": "Манга и манхва", "title": "Новые главы", "night": True, "sign": "part",
             "said": f"{agree(titles, CHECKED_TITLES)} {plural(titles, TITLES)}, новых глав: "
                     f"{int(result.get('got') or 0)}. Не ответили сайты:",
             "fails": [{"label": p.split(":", 1)[0], "why": p.split(":", 1)[-1].strip()} for p in problems],
             "retry": None, "hentai": False, "summary": True})


def _galleries_ended(task, result: dict) -> None:
    items = [x for x in result.get("items") or [] if isinstance(x, dict)]
    if not items:
        if task.status == "failed":
            add({"kind": "manga", "what": "Хентай", "title": "Галереи nhentai", "sign": "fail",
                 "said": "Не скачалось: " + _clean(task.error), "fails": [], "retry": None, "hentai": True})
        return
    came = [x for x in items if x.get("state") in ("done", "had")]
    left = [x for x in items if x.get("state") not in ("done", "had")]
    name = (lambda x: str(x.get("title") or f"Галерея {x.get('number')}"))
    fails = [{"label": name(x), "why": _clean(x.get("why")) if x.get("state") == "failed" else LEFT,
              "number": int(x.get("number") or 0)} for x in left]
    stopped = bool(result.get("stopped"))
    total = len(items)
    n = len(came)
    if stopped:
        said, sign = f"Остановлено: {agree(n, GOT).lower()} {plural(n, GALLERIES)} из {total}.", "stop"
    elif fails and came:
        said, sign = f"{agree(n, GOT)} {plural(n, GALLERIES)} из {total}. {agree(len(fails), NOT_GOT)} {len(fails)}:", \
            "part"
    elif fails:
        said, sign = f"Не скачалось ни одной галереи из {total}:", "fail"
    else:
        said, sign = f"{agree(n, GOT)} {plural(n, GALLERIES)}.", "ok"
    folders = [str(x.get("folder")) for x in came if x.get("folder")]
    add({"kind": "manga", "what": "Хентай", "title": name(items[0]) if total == 1 else f"Галереи nhentai — {total}",
         "sign": sign, "said": said, "fails": fails, "hentai": True, "folders": folders[:50],
         "retry": {"numbers": [f["number"] for f in fails if f["number"]]} if fails else None})


def _video_ended(task, result: dict) -> None:
    """(the other chat's download: its result and its series' folder are only read)"""
    title = re.sub(r"^Скачать с сайта:\s*", "", str(task.label or "")).strip()
    title = title[1:-1] if title.startswith("«") and title.endswith("»") else title
    folder = str(result.get("folder") or "")
    url = ""
    if folder:
        try:
            info = json.loads((Path(folder) / "info.json").read_text(encoding="utf-8"))
            url = str(info.get("source_url") or "") if isinstance(info, dict) else ""
        except (OSError, ValueError):
            url = ""
    if task.status == "failed" and not result.get("done"):
        add({"kind": "video", "what": "Видео", "title": title, "sign": "fail",
             "said": "Не скачалось: " + _clean(task.error or result.get("failed") or "причина в журнале"),
             "fails": [], "retry": {"url": url} if url.startswith(("http://", "https://")) else None,
             "folder": folder, "series": str(result.get("series") or ""), "url": url})
        return
    done, whole = int(result.get("done") or 0), int(result.get("whole") or 0)
    failed = int(result.get("failed_files") or 0)
    stopped = bool(result.get("stopped")) or task.status == "stopped"
    bits = [f"{agree(done, GOT_FILES)} {plural(done, FILES)}" if done else "Ничего не скачано"]
    if whole:
        bits.append(f"ещё {plural(whole, FILES)} — уже в папке")
    if failed:
        bits.append(f"{agree(failed, NOT_GOT_FILES)} {failed}")
    if result.get("merged"):
        bits.append("озвучки собраны в общие файлы серий")
    said = ("Остановлено: " + bits[0][:1].lower() + bits[0][1:] if stopped else bits[0]) + \
        "".join(", " + b for b in bits[1:]) + "."
    sign = "stop" if stopped else "part" if failed and done else "fail" if failed else "ok"
    fails = [{"label": f"Не скачались файлы: {failed}", "why": "причины — в журнале скачивания"}] if failed else []
    retry = {"url": url} if (failed or stopped) and url.startswith(("http://", "https://")) else None
    add({"kind": "video", "what": "Видео", "title": title, "sign": sign, "said": said, "fails": fails, "retry": retry,
         "folder": folder, "series": str(result.get("series") or ""), "url": url})


def _subs_ended(job) -> None:
    """«Галерея (booru)» and the subscriptions (their world is hidden in «SFW»)."""
    stopped = bool(getattr(job, "_stop_requested", False))
    if job.kind == "grab":
        sub = job.subs[0] if job.subs else {}
        cards = int(sub.get("cards") or 0)
        got = int(job.downloaded or 0)
        error = next((r.get("error") for r in job.results if r.get("error")), "")
        if stopped:
            said, sign = f"Остановлено: {agree(got, GOT).lower()} {plural(got, PICTURES)}" + (
                f" из {cards}." if cards else "."), "stop"
        elif error or job.status == "failed":
            said, sign = "Не скачалось: " + _clean(error or job.error), "fail"
        else:
            said = f"{agree(got, GOT)} {plural(got, PICTURES)}" + (
                f" из {cards} выбранных — остальные уже были в библиотеке или пропущены." if cards and got < cards
                else ".")
            sign = "ok"
        retry = None
        if stopped and sub.get("id"):
            try:
                from core.subscription_engine.seed_cache import stats_for_subscription
                stats = stats_for_subscription(str(sub["id"])) or {}
            except Exception as exc:  # noqa: BLE001 - no count: no «Докачать»
                _LOG.info("the grabber's seeds not counted: %s", exc)
                stats = {}
            pending = sum(int(v or 0) for k, v in stats.items()
                          if k in ("pending", "downloading", "failed", "failed_temp", "auth_required"))
            if pending:
                retry = {"sub": {k: sub.get(k) for k in ("id", "name", "query", "sites", "run_direction", "cards")},
                         "count": pending}
        add({"kind": "grab", "what": "Галерея (booru)", "title": str(sub.get("query") or job.label), "sign": sign,
             "said": said, "fails": [], "retry": retry, "gallery": True})
        return
    checked = [r for r in job.results if not r.get("skipped")]
    errors = [r for r in job.results if r.get("error")]
    skipped = [r for r in job.results if r.get("skipped")]
    fails = [{"label": str(r.get("name") or ""), "why": _clean(r.get("error"))} for r in errors]
    fails += [{"label": str(r.get("name") or ""), "why": "пропущена — проверится в следующий раз"} for r in skipped]
    said = (f"{agree(len(checked), CHECKED)} {plural(len(checked), SUBS)}, новых картинок: {int(job.downloaded or 0)}."
            + (" Остановлено." if stopped else ""))
    sign = "stop" if stopped else "part" if fails else "fail" if job.status == "failed" else "ok"
    add({"kind": "subs", "what": "Подписки", "title": str(job.label or "Подписки"), "sign": sign, "said": said,
         "fails": fails, "retry": None, "gallery": True})
