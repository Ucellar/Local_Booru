"""(v5.44.120) «Скачать → Загрузки» — one place for everything that downloads, one queue, «Готово», «Сама программа».

Step 4 of the plan after the owner's review of the whole program (mockup C, approved: «Норм»).  Until now «Загрузки»
were a column of «По ссылке» that knew three kinds (``get-link.js``, its copy on the phone ``phone-get.js``), and the
grabber and the subscriptions had their own pages.  Now:

* :func:`summary` — one answer for every kind that downloads (``/api/downloads/state``): the manga and manhwa of the
  sites (``manhwa_get``, ``core/manhwa_run``, ``core/manhwa_queue``; the nightly round of new chapters too), nhentai
  (``manga_get``, ``core/manga_download``), the videos (``video_get`` — the other chat's: its state, «Пауза»,
  «Остановить», «убрать» are only called, never changed), «Галерея (booru)» and the subscriptions
  (``core/subscription_jobs``).  Each row: the kind in a word, the title, its cover (the addresses there are), what goes
  now («Том 5 · Глава 45: страница 18 из 32», «Серия 03 · 61% из 412 МБ», «Скачано картинок: 34 из 120»), the bar, the
  speed and the time left when known, its state and the buttons it has — a button is there only when it works:
  «Пропустить главу» (``manhwa_run.skip``), «Пропустить галерею» (``manga_download.skip``), «Пропустить подписку»
  (``SubscriptionJob.skip``); the videos have no skip.  The queue — one list with places; «↑↓» within a kind's own
  queue (the manga and manhwa — ``manhwa_queue.move``, nhentai — ``manga_download.move``; the videos' order is theirs).
* «SFW» — decided here for every kind alike: a download of hentai (the manga and manhwa as ``manhwa_get`` decides it,
  nhentai always, a video by ``core/content_marks`` of its series and its site), «Галерея (booru)» and the
  subscriptions (the gallery's world) — «Скрытая загрузка 18+»: no picture, no name, no site, no folder, no journal, no
  link — the numbers and the buttons only; their covers are not asked (and answer 404).
* :func:`light_count` — what the badge «Загрузки» in the header of every page shows (going + waiting): from memory, no
  disk, no site.
* «Готово» — ``core/download_history`` (7 days); «Повторить не скачавшееся» puts exactly what did not come back in the
  queue (the manga and manhwa — ``manhwa_queue.start`` with those chapters; nhentai — those galleries; a video — its
  card on «По ссылке», nothing of its code changed; «Галерея (booru)» stopped — its posts left).
* «Сама программа» — what the program does without the person (``core/background_duties``): the same switches as in
  «Настройки» (``settings_store.save``), «идёт сейчас» / «было в 03:12 — итог» / «выключено», «Проверить сейчас» where
  there is one already (the comics, the subscriptions).

The pages: ``/get/downloads`` (a part of «Скачать», ``get_hub.PARTS``) and ``/m/get/downloads`` (the phone's «Скачать →
Загрузки»), one script for both (``static/downloads.js``), polled while the page is seen.
"""
from __future__ import annotations

import json
import hashlib
import logging
import re
import threading
import time
from pathlib import Path

from webui.html import esc
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.downloads")

ADDRESS = "/get/downloads"
PHONE_ADDRESS = "/m/get/downloads"
RUNNING = ("starting", "running", "stopping")
BIG = 10 ** 9                       # «after» of a journal: no lines
DONE_SHOWN = 8                      # titles of «Готово» shown before «Вся история за 7 дней»
FAILS_SHOWN = 6                     # what did not come, named under a line of «Готово»
EARLIER_SHOWN = 12                  # a title's earlier downloads named under its last one
DOWNLOADS = ("скачивание", "скачивания", "скачиваний")
CHECKS = ("проверка", "проверки", "проверок")
CHAPTERS = ("глава", "главы", "глав")
GALLERIES = ("галерея", "галереи", "галерей")
FILES = ("файл", "файла", "файлов")
SUBS = ("подписка", "подписки", "подписок")
TITLES = ("тайтл", "тайтла", "тайтлов")
STRIPS = ("полоса", "полосы", "полос")
DUTY_KEYS = {"night": "manhwa_watch_auto", "comics": "comic_manga_auto", "archive": "manga_archive_auto",
             "posts": "card_sets_ask_sites", "originals": "automatic_quality_upgrade_enabled",
             "trash": "trash_auto_purge_days"}
TRASH_DAYS = ((0, "только вручную"), (7, "через 7 дней"), (30, "через 30 дней"))
_FOLLOWED: dict = {"at": 0.0, "n": 0}
_FOLLOWED_LOCK = threading.Lock()
FOLLOWED_TTL = 60.0


def plural(n: int, forms: tuple) -> str:
    from webui.pages.post_sets import plural as words
    return words(int(n), forms)


def register(router) -> None:
    from core import download_history
    router.add("GET", r"/get/downloads", page)
    router.add("GET", r"/m/get/downloads", phone_page)
    router.add("GET", r"/(?:m/)?get/downloads/open", open_line)
    router.add("GET", r"/api/downloads/state", state_api)
    router.add("GET", r"/api/downloads/count", count_api)
    router.add("GET", r"/api/downloads/done", done_api)
    router.add("GET", r"/api/downloads/duties", duties_api)
    router.add("GET", r"/api/downloads/log", log_api)
    router.add("GET", r"/api/downloads/video-cover", video_cover)
    router.add("POST", r"/api/downloads/act", act_api)
    router.add("POST", r"/api/downloads/all", all_api)
    router.add("POST", r"/api/downloads/retry", retry_api)
    router.add("POST", r"/api/downloads/duty", duty_api)
    router.add("POST", r"/api/downloads/duty-act", duty_act_api)
    download_history.install()                       # the ends of downloads noted from now on


# ── words ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _when(stamp: float, now: float | None = None) -> str:
    """«сегодня в 03:12», «вчера в 22:40», «5.11 в 14:00»."""
    if not stamp:
        return ""
    now = time.time() if now is None else now
    day, today = time.localtime(stamp), time.localtime(now)
    clock = time.strftime("%H:%M", day)
    if day.tm_year == today.tm_year and day.tm_yday == today.tm_yday:
        return f"сегодня в {clock}"
    yesterday = time.localtime(now - 24 * 3600)
    if day.tm_year == yesterday.tm_year and day.tm_yday == yesterday.tm_yday:
        return f"вчера в {clock}"
    return f"{day.tm_mday}.{day.tm_mon:02d} в {clock}"


def _megabytes(n) -> str:
    n = float(n or 0)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} ГБ".replace(".", ",")
    return f"{max(1, round(n / 1024 ** 2))} МБ"


def _speed(n) -> str:
    n = float(n or 0)
    return f"{max(1, round(n / 1024))} КБ/с" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} МБ/с".replace(".", ",")


def _clock(seconds) -> str:
    minutes = max(1, round(float(seconds or 0) / 60))
    return f"{minutes} мин" if minutes < 60 else f"{minutes // 60} ч" + (f" {minutes % 60} мин" if minutes % 60 else "")


def _unquote(label: str, prefix: str) -> str:
    text = re.sub(prefix, "", str(label or "")).strip()
    return text[1:-1] if text.startswith("«") and text.endswith("»") else text


# ── what the pages ask: the state of every kind ─────────────────────────────────────────────────────────────────────
def _call(handler, settings: dict, query: dict | None = None, body: dict | None = None) -> tuple:
    """A page's own handler asked as its page asks it (its answer is the same the pages had): (status, its JSON)."""
    req = Request("POST" if body is not None else "GET", "", {}, {k: [str(v)] for k, v in (query or {}).items()}, {},
                  json.dumps(body or {}).encode("utf-8") if body is not None else b"", dict(settings or {}), None, "")
    answer = handler(req)
    try:
        data = json.loads(answer.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        data = {}
    return answer.status, data if isinstance(data, dict) else {}


def _line(task) -> str:
    """The newest line of a task's journal ("" — none)."""
    if task is None:
        return ""
    last = int(task.snapshot(after=BIG).get("last_seq") or 0)
    lines = task.snapshot(after=max(0, last - 1)).get("log") or []
    return str(lines[-1]["text"]) if lines else ""


def _video_records() -> list:
    """The downloads of the videos kept for the next start (``video_queue.json`` of ``video_get`` — only read): their
    pages' addresses by title, for «SFW»."""
    from core.file_safety import read_json_kept
    from core.paths import SETTINGS_DIR
    from webui.pages import video_get
    try:
        data = read_json_kept(Path(SETTINGS_DIR) / video_get.KEEP_NAME, dict, what="video queue") or {}
    except OSError:
        return []
    return [r for r in data.get("downloads") or [] if isinstance(r, dict)]


def _video_marks(st: dict, settings: dict) -> None:
    """(«SFW») which of the videos going and waiting are hentai — ``content_marks`` of their series (by its folder or
    its page) and of their page's site; the other chat's download is only read."""
    from types import SimpleNamespace
    from core import content_marks as CM, page_jobs, video_library as V
    from webui.pages import video_get
    root = V.video_root(settings)
    try:
        library = V.LIBRARY.items(root) if root and Path(root).is_dir() else []
    except OSError:
        library = []
    records = _video_records()

    def address_of(title: str) -> str:
        return next((str(r.get("address") or "") for r in records if str(r.get("title") or "") == title), "")

    def hentai(title: str, address: str = "", folder: str = "", facts: dict | None = None) -> bool:
        if CM.adult_site(address):
            return True
        if facts and CM.series_reason(SimpleNamespace(info={**facts, "source_url": address})):
            return True
        for series in library:
            if (folder and Path(series.path) == Path(folder)) or (address and str(series.info.get("source_url") or "")
                                                                  == address) or (title and series.title == title):
                return CM.series_verdict(series).hentai
        return False
    title = _unquote(st.get("label"), r"^Скачать с сайта:\s*")
    if st.get("status") in RUNNING:
        task = page_jobs.current(video_get.FIND)
        result = task.result if task is not None and isinstance(task.result, dict) else {}
        found = result.get("found") if task is not None and str(task.id) == str(st.get("find")) else None
        if found is not None:
            st["hentai"] = hentai(title, str(found.page.url or ""), str(found.folder), dict(found.facts or {}))
        else:
            st["hentai"] = hentai(title, address_of(title))
    for q in st.get("queue") or []:
        q["hentai"] = hentai(str(q.get("title") or ""), address_of(str(q.get("title") or "")))
    soon = st.get("restoring")
    if soon:
        soon["hentai"] = [hentai(t, address_of(t)) for t in soon.get("titles") or []]


def _subs_raw() -> dict:
    from core import subscription_jobs as J
    job = J.current()
    if job is None:
        return {"status": "idle"}
    snap = job.snapshot(after=BIG)
    sub = job.subs[0] if job.subs else {}
    one_off = job.kind in ("grab", "favorites")          # (v5.44.141) an account's favorites: as the grabber's
    snap.update(can_skip=job.can_skip(), cards=int(sub.get("cards") or 0) if one_off else 0,
                query=str(sub.get("query") or "") if job.kind == "grab" else "",
                name=str(sub.get("name") or "") if job.kind == "favorites" else "", going=not job.finished_at)
    return snap


def raw_states(settings: dict) -> dict:
    """Every kind's state as its own page has it (``download-state`` of ``manhwa_get``, ``manga_get``, ``video_get``;
    the check of subscriptions), and what «SFW» needs of the videos."""
    from core import page_jobs
    from webui import content_mode
    from webui.pages import manga_get, manhwa_get, video_get
    out: dict = {}
    for key, handler, query in (("manhwa", manhwa_get.download_state, {"items": "none", "after": BIG}),
                                ("manga", manga_get.download_state, {"after": BIG}),
                                ("video", video_get.download_state, {"after": BIG})):
        try:
            _status, out[key] = _call(handler, settings, query)
        except Exception as exc:  # noqa: BLE001 - one kind that cannot be read: the others are shown
            _LOG.warning("downloads: the state of %s not read: %s: %s", key, type(exc).__name__, exc)
            out[key] = {"status": "idle"}
    manhwa = out["manhwa"]
    if manhwa.get("status") in RUNNING and not manhwa.get("hidden"):
        from core import manhwa_download as D, manhwa_run as R
        manhwa["items"] = R.meter(since=0, run=-1)[1]
        manhwa["line"] = _line(page_jobs.current(D.TASK))
    for key, kind in (("manga", manga_get.GET), ("video", video_get.GET)):
        if out[key].get("status") in RUNNING and not out[key].get("hidden"):
            out[key]["line"] = _line(page_jobs.current(kind))
    if out["manga"].get("status") in RUNNING:
        from core import manga_download
        out["manga"]["paused"] = manga_download.paused()
    if content_mode.mode() == "sfw":
        try:
            _video_marks(out["video"], settings)
        except Exception as exc:  # noqa: BLE001 - not known: hidden (better a name not shown than a name of hentai)
            _LOG.info("downloads: the videos' marks not read: %s", exc)
            out["video"]["hentai"] = True
            for q in out["video"].get("queue") or []:
                q["hentai"] = True
    out["subs"] = _subs_raw()
    return out


# ── the rows ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _row(key: str, kind: str, title: str, **values) -> dict:
    row = {"key": key, "kind": kind, "title": title, "hidden": False, "dest": "", "cover": "", "state": "running",
           "text": "", "sub": "", "part": None, "speed": "", "left": "", "pause": False, "resume": False, "skip": "",
           "stop": True, "forget": False, "log": True, "task": 0}
    row.update(values)
    return row


def _hide_row(row: dict) -> dict:
    from webui.pages.manhwa_get import HIDDEN, HIDDEN_KIND
    row.update(title=HIDDEN, kind=HIDDEN_KIND, hidden=True, dest="", cover="", log=False)
    return row


def _manhwa_rows(st: dict, out: dict) -> None:
    from core import background_duties
    from webui.pages.manhwa_get import HIDDEN, HIDDEN_KIND
    hidden = bool(st.get("hidden"))
    current = ""
    if st.get("status") in RUNNING:
        m = st.get("meter") or {}
        if m.get("finished"):                        # the meter of a download that has ended (the nightly round
            m = {}                                   # between two titles): its line, not the old numbers
        night = st.get("label") == background_duties.ROUND_LABEL
        title = str(m.get("title") or "") or _unquote(st.get("label"), r"^Скачать:\s*")
        current = HIDDEN if hidden else title
        paused = bool(st.get("paused"))
        # (v5.44.163) the round of new chapters waits for the person to leave the computer: a pause of the program's
        # own — its words, «Продолжить» lets it go on now (``manhwa_run.wait_away``, ``go_on``)
        held = bool(st.get("held")) and not paused and st.get("status") != "stopping"
        now = m.get("now") if isinstance(m.get("now"), dict) else None
        total = int(m.get("total") or 0)
        done, failed = int(m.get("done") or 0), int(m.get("failed") or 0)
        manga = m.get("kind") == "manga"
        row = _row("manhwa", st.get("what") or "Манхва", title or ("Новые главы" if night else ""), task=st.get("id"),
                   dest="в папку манги" if manga else "в папку манхвы",
                   state="paused" if paused or held else "running",
                   pause=not paused and not held and st.get("status") != "stopping", resume=paused or held)
        if st.get("status") == "stopping":
            row.update(state="stopping", text="Останавливаю…")
        elif held:
            from core.manhwa_run import HELD_ROW
            row["text"] = HELD_ROW
            row["sub"] = " · ".join(x for x in (f"Глав: {done} из {total}" if total else "",
                                                "ночная проверка новых глав") if x)
            if total:
                row["part"] = min(1.0, (done + failed) / total)
        elif total:
            part = now["got"] / now["pages"] if now and now.get("pages") else 0
            row["part"] = min(1.0, (done + failed + part) / total)
            if now:
                page = (f"страница {now.get('got', 0)} из {now['pages']}" if manga else
                        f"{now.get('got', 0)} из {plural(int(now['pages']), STRIPS)}") if now.get("pages") else ""
                where = "" if hidden else str(now.get("label") or "") + (f" (с {now['from']})" if now.get("from") else "")
                said = (where + (": " if where and page else "") + page) if not hidden else \
                    f"Глава {done + failed + 1} из {total}" + (f": {page}" if page else "")
            else:
                said = "" if hidden else str(st.get("line") or "")
            bits = [said] if said else []
            if m.get("speed") and not paused:
                bits.append(_speed(m["speed"]))
                row["speed"] = _speed(m["speed"])
            if m.get("eta") and not paused:
                bits.append(f"осталось ≈ {_clock(m['eta'])}")
                row["left"] = _clock(m["eta"])
            row["text"] = ("Пауза" + (" — " if bits else "") if paused else "") + " · ".join(bits)
            sub = [f"Глав: {done} из {total}"]
            retry = [x for x in st.get("items") or [] if x.get("state") == "retry"]
            seeking = [x for x in retry if x.get("seek")]      # (v5.44.146) the other sites asked once more
            if seeking:
                sub.append("ищу на других сайтах" + ("" if hidden else f": {seeking[0].get('label')}"))
            elif m.get("retrying"):
                first = retry[0] if retry and not hidden else None
                sub.append(f"ждёт повтора: {m['retrying']}" + (f" — {first.get('label')}, через {first.get('again', 0)} с"
                                                               if first else ""))
            if failed:
                sub.append(f"не скачались: {failed}")
            if night:
                sub.append("ночная проверка новых глав")
            row["sub"] = " · ".join(sub)
            if now and now.get("state") in ("going", "paused") and st.get("status") != "stopping":
                row["skip"] = "Пропустить главу"
        else:
            row["text"] = "" if hidden else str(st.get("line") or "Начинаю…")
            if night:
                row["sub"] = "ночная проверка новых глав"
        if m.get("run") and not hidden and (now or done):
            from core.title_covers import stamp       # (v5.44.123) a cover chosen meanwhile: another address
            row["cover"] = f"/api/manhwa/download-cover?run={int(m['run'])}" + (
                f"&v={stamp(Path(str(m['folder'])))}" if m.get("folder") else "")
        out["jobs"].append(_hide_row(row) if hidden else row)
    _queue(out, "manhwa", st.get("queue") or [], current, move=True,
           what=lambda q: (plural(int(q.get("count") or 0), CHAPTERS) if q.get("count") else "все новые главы"),
           hidden=lambda q: q.get("title") == HIDDEN)
    soon = st.get("restoring")
    if soon:
        titles = list(soon.get("titles") or [])
        row = _row("manhwa", "Манга и манхва", ", ".join(f"«{t}»" for t in titles), state="restoring", stop=False,
                   forget=True, log=False, count=len(titles),
                   text=f"С прошлого запуска программы продолжится — через {int(soon.get('in') or 0)} с"
                   if int(soon.get("in") or 0) > 0 else "С прошлого запуска программы — продолжается сейчас")
        if all(t == HIDDEN for t in titles):
            row.update(title=HIDDEN, kind=HIDDEN_KIND, hidden=True)
        out["jobs"].append(row)


def _queue(out: dict, key: str, queue: list, current: str, *, move: bool, what, hidden) -> None:
    from webui.pages.manhwa_get import HIDDEN, HIDDEN_KIND
    words = {"manhwa": "Манга и манхва", "video": "Видео", "manga": "Хентай"}
    folders = {"manga": "Манга", "manhwa": "Манхва"}  # the folder chosen on the card (none — by the title's type)
    before = current
    for n, q in enumerate(queue):
        secret = bool(hidden(q))
        title = HIDDEN if secret else str(q.get("title") or "")
        after = f"начнётся после «{before}»" if before else "начнётся первой"
        if key == "manga":
            after = "в той же загрузке nhentai, после текущей" if n == 0 else f"после «{before}»"
        told = what(q)
        word = folders.get(str(q.get("where") or ""), words[key]) if key == "manhwa" else words[key]
        out["queue"].append({"key": key, "id": q.get("id"), "kind": HIDDEN_KIND if secret else word,
                             "hidden": secret, "title": title, "what": " · ".join(x for x in (told, after) if x),
                             "up": move and n > 0, "down": move and n < len(queue) - 1, "drop": True})
        before = title


def _video_rows(st: dict, out: dict) -> None:
    from webui.pages.manhwa_get import HIDDEN, HIDDEN_KIND
    hidden = bool(st.get("hentai"))
    current = ""
    if st.get("status") in RUNNING:
        m = st.get("meter") or {}
        paused = bool(st.get("paused"))
        title = _unquote(st.get("label"), r"^Скачать с сайта:\s*")
        current = HIDDEN if hidden else title
        from core.title_covers import version       # (v5.44.123) a poster chosen meanwhile: another address
        chosen = version()
        row = _row("video", "Видео", title, task=st.get("id"), dest="в папку видео",
                   state="paused" if paused else "running", pause=not paused and st.get("status") != "stopping",
                   resume=paused, cover="" if hidden else f"/api/downloads/video-cover?task={int(st.get('id') or 0)}"
                                                         + (f"&v={chosen}" if chosen else ""))
        if st.get("status") == "stopping":
            row.update(state="stopping", text="Останавливаю…")
        elif m.get("count"):
            count, index = int(m["count"]), int(m.get("index") or 1)
            if m.get("merging"):
                row["text"] = "Собираю озвучки в общие файлы серий" + ("" if hidden else f": {m.get('name') or ''}")
                row["part"] = max(0, index - 1) / count
            else:
                share = min(1.0, float(m.get("done") or 0) / float(m["total"])) if m.get("total") else 0
                row["part"] = min(1.0, (index - 1 + share) / count)
                name = f"Файл {index}" if hidden else re.sub(r"\.[^.]+$", "", str(m.get("name") or "Файл"))
                bits = [name]
                if m.get("total"):
                    bits.append(f"{int(share * 100)}% из {_megabytes(m['total'])}")
                elif m.get("done"):
                    bits.append(_megabytes(m["done"]))
                if m.get("speed") and not paused:
                    bits.append(f"{_megabytes(m['speed'])}/с")
                    row["speed"] = f"{_megabytes(m['speed'])}/с"
                row["text"] = ("Пауза — " if paused else "") + " · ".join(bits)
            row["sub"] = f"Файлов: {index} из {count}"
        else:
            row["text"] = "" if hidden else str(st.get("line") or "Начинаю…")
        out["jobs"].append(_hide_row(row) if hidden else row)
    _queue(out, "video", st.get("queue") or [], current, move=False,
           what=lambda q: plural(int(q.get("files") or 0), FILES) if q.get("files") else "",
           hidden=lambda q: bool(q.get("hentai")))
    soon = st.get("restoring")
    if soon:
        marks = soon.get("hentai") or []
        titles = [HIDDEN if k < len(marks) and marks[k] else t for k, t in enumerate(soon.get("titles") or [])]
        row = _row("video", "Видео", ", ".join(f"«{t}»" for t in titles), state="restoring", stop=False, log=False,
                   count=len(titles),
                   text=f"С прошлого запуска программы продолжится — через {int(soon.get('in') or 0)} с"
                   if int(soon.get("in") or 0) > 0 else "С прошлого запуска программы — продолжается сейчас")
        if titles and all(t == HIDDEN for t in titles):
            row.update(title=HIDDEN, kind=HIDDEN_KIND, hidden=True)
        out["jobs"].append(row)


def _manga_rows(st: dict, out: dict) -> None:
    from webui.pages.manhwa_get import HIDDEN, HIDDEN_KIND
    hidden = bool(st.get("hidden"))
    items = [x for x in st.get("items") or [] if isinstance(x, dict)]
    current = ""
    if st.get("status") in RUNNING:
        paused = bool(st.get("paused"))
        now = next((x for x in items if x.get("state") in ("pages", "reading")), None)
        done = sum(1 for x in items if x.get("state") in ("done", "had"))
        name = (lambda x: "Галерея" if hidden else str(x.get("title") or f"Галерея {x.get('number')}"))
        if len(items) == 1:
            title = name(items[0])
        else:
            title = f"Галереи nhentai — {len(items)}" if items else _unquote(st.get("label"), r"^.*nhentai:\s*")
        current = HIDDEN if hidden else (name(now) if now else title)
        row = _row("manga", "Хентай", title, task=st.get("id"), dest="в папку манги",
                   state="paused" if paused else "running", pause=not paused and st.get("status") != "stopping",
                   resume=paused)
        if st.get("status") == "stopping":
            row.update(state="stopping", text="Останавливаю…")
        elif now is not None:
            place = items.index(now) + 1
            page = (f"страница {now.get('page') or 1} из {now.get('pages') or '?'}" if now.get("state") == "pages"
                    else "читаю описание…")
            said = (f"{place} из {len(items)} · {page}" if hidden else
                    (f"Галерея {place} из {len(items)}: " if len(items) > 1 else "") + (page if len(items) > 1 else
                                                                                        f"{name(now)}: {page}"))
            row["text"] = ("Пауза — " if paused else "") + said
            share = (now.get("page") or 0) / now["pages"] if now.get("pages") else 0
            row["part"] = (done + share) / max(1, len(items))
            waits = sum(1 for x in items if x.get("state") == "wait")
            row["sub"] = f"Готово: {done}" + (f" · дальше в этой загрузке: {waits}" if waits else "")
            row["skip"] = "Пропустить галерею"
            covered = (lambda x: x.get("cover") and (x.get("state") in ("done", "had") or (x.get("state") == "pages"
                                                                                            and (x.get("page") or 0) > 1)))
            pic = now if covered(now) else next((x for x in items if covered(x)), None)
            if pic is not None and not hidden:
                row["cover"] = str(pic["cover"])
        else:
            row["text"] = "" if hidden else str(st.get("line") or "Начинаю…")
        out["jobs"].append(_hide_row(row) if hidden else row)
    queue = [{"id": q.get("id"), "title": q.get("title")} for q in st.get("queue") or []] \
        if st.get("status") in RUNNING else []
    _queue(out, "manga", queue, current, move=True, what=lambda q: "", hidden=lambda q: hidden)
    soon = st.get("restoring")
    if soon:
        titles = list(soon.get("titles") or [])
        row = _row("manga", "Хентай", ", ".join(f"«{t}»" for t in titles), state="restoring", stop=False, forget=True,
                   log=False, count=len(titles),
                   text=f"С прошлого запуска программы продолжится — через {int(soon.get('in') or 0)} с"
                   if int(soon.get("in") or 0) > 0 else "С прошлого запуска программы — продолжается сейчас")
        out["jobs"].append(_hide_row(row) if hidden or titles == [HIDDEN] else row)


def _subs_rows(st: dict, out: dict, sfw: bool) -> None:
    if not st.get("going") or st.get("status") not in ("starting", "running", "waiting", "stopping"):
        return
    favorites = st.get("kind") == "favorites"            # (v5.44.141) an account's favorites — as the grabber's
    grab = st.get("kind") == "grab" or favorites
    paused = bool(st.get("paused"))
    got = int(st.get("downloaded") or 0)
    if favorites:
        word, title = "Избранное", str(st.get("name") or st.get("label") or "Избранное").replace("Избранное: ", "", 1)
    elif grab:
        word, title = "Галерея (booru)", str(st.get("query") or "выбранное")
    else:
        word, title = "Подписки", str(st.get("label") or "Подписки")
    row = _row("subs", word, title, task=st.get("id"), dest="в галерею", state="paused" if paused else "running",
               pause=not paused and st.get("status") != "stopping", resume=paused)
    if st.get("status") == "stopping":
        row.update(state="stopping", text="Останавливаю: докачиваю текущий файл…")
    elif st.get("status") == "waiting" and st.get("plan"):
        plan = st["plan"]
        row.update(state="waiting", text=f"Большая загрузка: {plural(int(plan.get('files') or 0), FILES)} — программа "
                                         "ждёт ответа «Скачать» или «Не скачивать» (окно программы, «Подписки»)")
    elif grab:
        cards = int(st.get("cards") or 0)
        row["text"] = ("Пауза — " if paused else "") + f"Скачано картинок: {got}" + (f" из {cards}" if cards else "")
        row["part"] = min(1.0, got / cards) if cards else None
    else:
        count, index = int(st.get("count") or 0), int(st.get("index") or 0)
        current = str(st.get("current") or "")
        named = f"«{current}» · " if current and not sfw else ""
        row["text"] = ("Пауза — " if paused else "") + f"{named}новых картинок пока: {got}"
        row["sub"] = f"Подписок: {index} из {count}" if count > 1 else ""
        row["part"] = max(0, index - 1) / count if count else None
    if st.get("can_skip") and st.get("status") != "stopping":
        row["skip"] = "Пропустить подписку"
    out["jobs"].append(_hide_row(row) if sfw else row)


def summary(raw: dict, *, sfw: bool = False) -> dict:
    """The state of «Загрузки» for the pages from every kind's state (:func:`raw_states`): the rows going, the queue,
    the counts (the header's words and the badge's number)."""
    out: dict = {"jobs": [], "queue": []}
    _manhwa_rows(raw.get("manhwa") or {}, out)
    _video_rows(raw.get("video") or {}, out)
    _manga_rows(raw.get("manga") or {}, out)
    _subs_rows(raw.get("subs") or {}, out, sfw)
    order = {"running": 0, "waiting": 0, "stopping": 0, "paused": 1, "restoring": 2}
    out["jobs"].sort(key=lambda r: order.get(r["state"], 3))
    going = sum(1 for r in out["jobs"] if r["state"] in ("running", "waiting", "stopping"))
    paused = sum(1 for r in out["jobs"] if r["state"] == "paused")
    queued = len(out["queue"]) + sum(int(r.get("count") or 1) for r in out["jobs"] if r["state"] == "restoring")
    out["counts"] = {"going": going, "paused": paused, "queued": queued}
    out["badge"] = going + paused + queued
    out["said"] = counts_words(going, paused, queued)
    return out


def counts_words(going: int, paused: int, queued: int) -> str:
    """«5 идут · 1 на паузе · 3 в очереди»."""
    parts = []
    if going:
        parts.append("1 идёт" if going == 1 else f"{going} идут")
    if paused:
        parts.append(f"{paused} на паузе")
    if queued:
        parts.append(f"{queued} в очереди")
    return " · ".join(parts) or "ничего не качается"


def light_count() -> int:
    """The badge «Загрузки» of the header: the downloads going (a pause too) and waiting — from memory only (no disk,
    no site): the same count as :func:`summary`'s ``badge``."""
    from core import manga_download, manhwa_download as D, manhwa_queue as Q, page_jobs, subscription_jobs as J
    from webui.pages import manga_get, video_get
    n = 0
    for kind in (D.TASK, manga_get.GET, video_get.GET):
        task = page_jobs.current(kind)
        if task is not None and task.running:
            n += 1
    job = J.current()
    if job is not None and not job.finished_at and job.status in ("starting", "running", "waiting", "stopping"):
        n += 1
    n += len(Q.public()) + len(video_get.queue_public())
    task = page_jobs.current(manga_get.GET)
    if task is not None and task.running:
        n += len(manga_download.waiting())
    for soon in (Q.restoring_public(), manga_download.restoring_public(), video_get.restoring_public()):
        if soon:
            n += len(soon.get("titles") or []) or 1
    return n


# ── the pages ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _sfw() -> bool:
    from webui import content_mode
    return content_mode.mode() == "sfw"


def _board(phone: bool) -> str:
    """The page's own part — the same on the computer and the phone (``static/downloads.js`` fills it)."""
    sfw = _sfw()
    note = ('<p class="dl-sfw" data-dl-sfw>Включён «SFW»: хентай, «Галерея (booru)» и подписки — «Скрытая загрузка 18+», '
            'без картинки и названия, но с ходом и кнопками.</p>') if sfw else ""
    return f"""<div class="dl{' dl-phone' if phone else ''}" id="downloads" data-sfw="{'1' if sfw else ''}"
  data-phone="{'1' if phone else ''}" data-open="{esc(PHONE_ADDRESS if phone else ADDRESS)}/open?id=">
  <div class="dl-head">
    <div class="dl-head-text">{'' if phone else '<h1>Загрузки</h1>'}<span class="dl-count" aria-live="polite"></span></div>
    <div class="dl-head-tools" hidden>
      <button type="button" class="dl-btn big" data-all="pause">Пауза всего</button>
      <button type="button" class="dl-btn big danger" data-all="stop">Остановить всё</button>
    </div>
  </div>
  {note}
  <div class="dl-board">
    <div class="dl-main">
      <section class="dl-panel dl-going" aria-labelledby="dl-going-title">
        <h2 id="dl-going-title">Идёт сейчас <span class="dl-n"></span></h2>
        <ol class="dl-jobs"></ol>
        <p class="dl-empty">Пока ничего не качается. Найдите тайтл, серию или галерею в «По ссылке» — «Скачать»
          поставит их сюда.</p>
      </section>
      <section class="dl-panel dl-queue-panel" aria-labelledby="dl-queue-title" hidden>
        <h2 id="dl-queue-title">Очередь <span class="dl-n"></span><small>начнутся сами, по порядку; ↑↓ — поменять
          порядок</small></h2>
        <ol class="dl-queue"></ol>
      </section>
      <section class="dl-panel dl-done-panel" aria-labelledby="dl-done-title">
        <h2 id="dl-done-title">Готово <small>что пришло и что нет — последнее по каждому тайтлу</small></h2>
        <ol class="dl-done"></ol>
        <p class="dl-empty dl-done-empty" hidden>За последние 7 дней загрузок не было.</p>
        <button type="button" class="dl-link dl-more" data-done-all hidden>Вся история за 7 дней</button>
      </section>
    </div>
    <aside class="dl-side">
      <section class="dl-panel dl-duties-panel" aria-labelledby="dl-duties-title">
        <h2 id="dl-duties-title">Сама программа</h2>
        <p class="dl-lead">Что программа делает без вас, пока она открыта. Выключатели — те же, что в «Настройках».</p>
        <ol class="dl-duties"></ol>
        <p class="dl-foot">Мелкие дела — длина видео, опенинги и эндинги серий, категории тегов, «фото или рисунок» —
          идут сами и здесь не показываются. Тяжёлые дела (проверка новых глав, опенинги и эндинги, «фото или
          рисунок») работают, только пока за компьютером никого нет: стоит тронуть мышь или клавишу — ждут.</p>
      </section>
    </aside>
  </div>
</div>"""


def page(req: Request) -> Response:
    """``/get/downloads`` — «Скачать → Загрузки» of the computer."""
    from webui.pages import get_hub
    return get_hub.frame(req, "Загрузки", _board(phone=False), part="downloads", kind="downloads",
                         assets=("downloads",))


def phone_chips(current: str, count: int | None = None) -> str:
    """(v5.44.120) The phone's «Скачать»: «По ссылке» and «Загрузки N» (``/m/get``, ``/m/get/downloads``)."""
    from webui.pages import phone
    from webui.pages.phone_get import LINK_ICON
    n = light_count() if count is None else count
    number = f'<b data-dl-n>{n}</b>' if n else '<b data-dl-n hidden></b>'
    links = (("link", "/m/get", f'{phone.svg(LINK_ICON, size=16, width=2)}&nbsp;По ссылке'),
             ("downloads", PHONE_ADDRESS, f"Загрузки{number}"))
    mark = ' aria-current="page"'
    return ('<nav class="m-chips m-get-chips" aria-label="Скачать">' + "".join(
        f'<a class="m-chip" href="{href}"{mark if key == current else ""}>{label}</a>'
        for key, href, label in links) + "</nav>")


def phone_page(req: Request) -> Response:
    """``/m/get/downloads`` — the phone's «Скачать → Загрузки»: the same board, the same script and API."""
    from webui.pages import phone
    body = phone_chips("downloads") + _board(phone=True)
    return phone.page("Загрузки", body, heading="Скачать", top=phone.mode_switch(), scripts=("downloads",),
                      head='\n<link rel="stylesheet" href="/static/downloads-phone.css">')


def badge_html(count: int | None = None) -> str:
    """The badge «Загрузки» in the header of every page of the computer (``html.page_frame``): the downloads going and
    waiting; none — the badge without a number (``booru.js`` asks for the number while the page is seen)."""
    try:
        n = light_count() if count is None else int(count)
    except Exception as exc:  # noqa: BLE001 - a count not had: the badge is there without a number
        _LOG.debug("downloads: no count for the badge: %s", exc)
        n = 0
    icon = ('<svg class="dl-badge-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 4v11M7 '
            '10.5l5 5 5-5M5 19.5h14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
            'stroke-linejoin="round"/></svg>')
    number = f'<b class="dl-badge-n" data-dl-n>{n}</b>' if n else '<b class="dl-badge-n" data-dl-n hidden></b>'
    return (f'<a class="skin-button dl-badge{" on" if n else ""}" href="{ADDRESS}" data-dl-badge title="Загрузки — что '
            f'качается и в очереди">{icon}<span class="skin-label">Загрузки</span>{number}</a>')


def phone_badge(count: int | None = None) -> str:
    """⬇ with the number in the bar of every page of the phone (``phone.phone_frame``)."""
    from webui.pages import phone
    try:
        n = light_count() if count is None else int(count)
    except Exception:  # noqa: BLE001 - without a number
        n = 0
    number = f'<b class="m-dl-n" data-dl-n>{n}</b>' if n else '<b class="m-dl-n" data-dl-n hidden></b>'
    return (f'<a class="m-icon m-dl" href="{PHONE_ADDRESS}" data-dl-badge aria-label="Загрузки">'
            f'{phone.svg(phone.SAVE, width=2)}{number}</a>')


# ── the API ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def state_api(req: Request) -> Response:
    from core import download_history
    sfw = _sfw()
    answer = summary(raw_states(req.settings), sfw=sfw)
    return Response.json({"ok": True, "sfw": sfw, "done_rev": download_history.revision(), **answer})


def count_api(req: Request) -> Response:
    return Response.json({"ok": True, "count": light_count()})


def _entry_hidden(entry: dict, settings: dict) -> bool:
    """A line of «Готово» hidden by «SFW»: nhentai, the gallery's world, the manga and manhwa of hentai (as their
    downloads are judged — a mark by hand later too), a video of hentai."""
    kind = entry.get("kind")
    if kind in ("manga", "grab", "subs") or entry.get("gallery"):
        return True
    if entry.get("hentai"):
        return True
    if kind == "manhwa" and entry.get("folder"):
        from webui.pages.manhwa_get import hentai_download
        try:
            return hentai_download(settings, {"site": entry.get("site"), "kind": entry.get("folder_kind"),
                                              "folder": entry.get("folder")})
        except Exception:  # noqa: BLE001 - not known: hidden
            return True
    if kind == "video":
        from types import SimpleNamespace
        from core import content_marks as CM, video_library as V
        if CM.adult_site(str(entry.get("url") or "")):
            return True
        folder, title = str(entry.get("folder") or ""), str(entry.get("title") or "")
        root = V.video_root(settings)
        try:                                         # its series by its folder, else by its name (as the rows do)
            library = V.LIBRARY.items(root) if root and Path(root).is_dir() else []
        except OSError:
            return True
        series = next((x for x in library if folder and Path(x.path) == Path(folder)), None) or \
            next((x for x in library if title and x.title == title), None)
        if series is not None:
            return CM.series_verdict(series).hentai
        return bool(CM.series_reason(SimpleNamespace(info={"source_url": entry.get("url") or ""})))
    return False


def _earlier(entries: list, hidden: bool, now: float) -> dict:
    """(v5.44.151) A title's downloads before its last one, for the fold under it: {"words": «Раньше — ещё 3
    скачивания за 7 дней (1 — не полностью)», "lines": [{"sign", "when", "what"}]} — {} when there were none."""
    if not entries:
        return {}
    checks = all(x.get("kind") == "subs" or x.get("summary") for x in entries)
    amiss = sum(1 for x in entries if x.get("sign") in ("part", "fail"))
    words = f"Раньше — ещё {plural(len(entries), CHECKS if checks else DOWNLOADS)} за 7 дней"
    if amiss:
        words += f" ({amiss} — не полностью)" if not checks else f" ({amiss} — с ошибками)"
    lines = []
    for entry in entries[:EARLIER_SHOWN]:
        said = re.sub(r":\s*$", ".", str(entry.get("said") or ""))
        if hidden:
            said = re.sub(r"«[^»]*»", "", said).strip()
        when = _when(float(entry.get("at") or 0), now) + (" · ночная проверка" if entry.get("night") else "")
        lines.append({"sign": entry.get("sign") or "ok", "when": when, "what": said})
    if len(entries) > EARLIER_SHOWN:
        lines.append({"sign": "", "when": "", "what": f"…и ещё {len(entries) - EARLIER_SHOWN}"})
    return {"words": words, "lines": lines}


def _done_row(entry: dict, settings: dict, sfw: bool, now: float, earlier: list | None = None) -> dict:
    from webui.pages.manhwa_get import HIDDEN, HIDDEN_KIND
    hidden = sfw and _entry_hidden(entry, settings)
    fails = list(entry.get("fails") or [])
    retry = entry.get("retry") if isinstance(entry.get("retry"), dict) else None
    tool = None
    if retry and entry.get("kind") == "video" and retry.get("url"):
        from webui.pages.get_link import link_of
        tool = {"label": "Повторить не скачавшееся", "href": link_of(str(retry["url"])), "count": len(fails)}
    elif retry and entry.get("kind") in ("grab", "favorites"):
        tool = {"label": f"Докачать остальные ({int(retry.get('count') or 0)})", "id": entry["id"]}
    elif retry:
        count = len(retry.get("chapters") or retry.get("numbers") or [])
        if count:
            tool = {"label": f"Повторить не скачавшееся ({count})", "id": entry["id"]}
    can_open = entry.get("kind") in ("manhwa", "video", "manga") and not entry.get("summary") and \
        bool(entry.get("folder") or entry.get("series") or entry.get("folders"))
    when = _when(float(entry.get("at") or 0), now) + (" · ночная проверка" if entry.get("night") else "")
    row = {"id": entry["id"], "sign": entry.get("sign") or "ok", "kind": entry.get("what") or "", "hidden": hidden,
           "title": entry.get("title") or "", "when": when, "what": entry.get("said") or "",
           "fails": [f"{f.get('label')} — {f.get('why')}" if f.get("why") else str(f.get("label") or "")
                     for f in fails[:FAILS_SHOWN]] + ([f"…и ещё {len(fails) - FAILS_SHOWN}"]
                                                       if len(fails) > FAILS_SHOWN else []),
           "note": str(entry.get("note") or ""),       # (v5.44.146) where the chapters were looked for
           "retry": tool, "open": can_open,
           "earlier": _earlier(earlier or [], hidden, now)}     # (v5.44.151) the title's downloads before this one
    if hidden:
        numbers = re.sub(r":\s*$", ".", str(entry.get("said") or ""))
        numbers = re.sub(r"«[^»]*»", "", numbers).strip()
        row.update(title=HIDDEN, kind=HIDDEN_KIND, fails=[], open=False, what=numbers, note="")
        if tool and tool.get("href"):
            row["retry"] = None                       # a link would name the title's page
    return row


def done_api(req: Request) -> Response:
    """«Готово»: a row a title — its last download, the earlier ones folded under it (``download_history.grouped``);
    the titles downloaded last (``all=1`` — of the whole 7 days)."""
    from core import download_history
    sfw = _sfw()
    now = time.time()
    groups = download_history.grouped(now)
    shown = groups if req.arg("all") == "1" else groups[:DONE_SHOWN]
    rows = []
    for group in shown:
        row = _done_row(group[0], req.settings, sfw, now, group[1:])
        # what the page remembers an unfolded title by (not its name: a row hidden by «SFW» has none)
        row["key"] = hashlib.md5(download_history.title_key(group[0]).encode("utf-8")).hexdigest()[:12]
        rows.append(row)
    return Response.json({"ok": True, "rev": download_history.revision(), "total": len(groups), "rows": rows})


def _task_of(key: str):
    from core import manhwa_download as D, page_jobs
    from webui.pages import manga_get, video_get
    kind = {"manhwa": D.TASK, "manga": manga_get.GET, "video": video_get.GET}.get(key)
    return page_jobs.current(kind) if kind else None


def log_api(req: Request) -> Response:
    """«Подробности»: the journal of the download of ``key`` going (``after`` — the lines after that one); a download
    «SFW» hides — none (its lines name it)."""
    key = req.arg("key")
    after = req.int_arg("after", 0, lo=0)
    rows = summary(raw_states(req.settings), sfw=_sfw())["jobs"]
    if any(r["key"] == key and r["hidden"] for r in rows):
        return Response.json({"ok": True, "hidden": True, "log": [], "last_seq": 0})
    if key == "subs":
        from core import subscription_jobs as J
        job = J.current()
        if job is None:
            return Response.json({"ok": True, "log": [], "last_seq": 0})
        snap = job.snapshot(after=after)
        return Response.json({"ok": True, "id": snap["id"], "log": snap["log"], "last_seq": snap["last_seq"]})
    task = _task_of(key)
    if task is None:
        return Response.json({"ok": True, "log": [], "last_seq": 0})
    snap = task.snapshot(after=after)
    return Response.json({"ok": True, "id": snap["id"], "log": snap["log"], "last_seq": snap["last_seq"]})


def video_cover(req: Request) -> Response:
    """The poster of the video going (``task`` — its number): the one its «Найти» found, else the series folder's;
    none of a video «SFW» hides."""
    from core import page_jobs
    from webui.pages import video_get
    task = page_jobs.current(video_get.GET)
    if task is None or not task.running or req.arg("task") != str(task.id):
        return Response.not_found("Обложки нет")
    if _sfw():
        st = {"status": "running", "label": task.label, "find": task.about, "queue": []}
        try:
            _video_marks(st, req.settings)
        except Exception:  # noqa: BLE001 - not known: no cover
            return Response.not_found("Обложки нет")
        if st.get("hentai"):
            return Response.not_found("Обложки нет")
    kinds = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
             ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp"}
    find = page_jobs.current(video_get.FIND)
    result = find.result if find is not None and isinstance(find.result, dict) else {}
    found = result.get("found") if find is not None and str(find.id) == str(task.about) else None
    if found is not None:                    # (v5.44.123) the poster the owner chose in the menu of covers
        from webui.pages.covers import series_picture
        chosen = series_picture(str(getattr(getattr(found, "page", None), "url", "") or ""))
        if chosen is not None:
            return Response(200, bytes(chosen[0]), chosen[1], {"Cache-Control": "private, max-age=600"})
    if found is not None and found.poster:
        data, ext, _where = found.poster
        return Response(200, bytes(data), kinds.get(str(ext).lower(), "image/jpeg"), {"Cache-Control": "private, max-age=600"})
    if found is not None:
        from core import video_library as V
        for path in V.series_poster_names(Path(found.folder)):
            try:
                if path.is_file() and path.stat().st_size <= 20 * 1024 * 1024:
                    return Response(200, path.read_bytes(), kinds.get(path.suffix.lower(), "image/jpeg"),
                                    {"Cache-Control": "private, max-age=600"})
            except OSError:
                continue
    return Response.not_found("Обложки нет")


# ── what the buttons do ─────────────────────────────────────────────────────────────────────────────────────────────
def act(settings: dict, key: str, what: str, number=None, step: int = 0) -> tuple:
    """One button of a row or of the queue: (ok, words).  ``what`` — pause, resume, stop, skip, forget, drop, move."""
    from core import manga_download as MD, manhwa_queue as Q, manhwa_run as R, subscription_jobs as J
    from webui.pages import manga_get, manhwa_get, video_get
    if key == "manhwa":
        if what in ("pause", "resume"):
            status, data = _call(manhwa_get.download_pause, settings, body={"pause": what == "pause"})
            return status == 200, data.get("error") or ("Пауза: глава остановится, где она есть" if what == "pause"
                                                          else "Продолжаю")
        if what == "stop":
            status, data = _call(manhwa_get.download_stop, settings, body={})
            return status == 200, data.get("error") or "Останавливаю…"
        if what == "skip":
            return (True, "Пропускаю главу — качаю следующую") if R.skip() else (False, "Сейчас ни одна глава не качается")
        if what == "forget":
            return (True, "Скачивание с прошлого запуска не продолжится") if Q.forget_restore() else \
                (False, "Они уже продолжаются — остановить можно «Остановить»")
        if what == "drop":
            return (True, "Убрано из очереди") if Q.unqueue(number) else (False, "Его уже нет в очереди")
        if what == "move":
            return (True, "Порядок поменялся") if Q.move(number, step) else (False, "Дальше двигать некуда")
    if key == "manga":
        task = _task_of("manga")
        going = task is not None and task.running
        if what in ("pause", "resume"):
            if not going:
                return False, "Скачивание не идёт"
            (MD.pause if what == "pause" else MD.resume)()
            task._say("ПАУЗА: следующая страница ждёт «Продолжить»" if what == "pause" else "ПРОДОЛЖАЮ")
            return True, "Пауза: страница докачается, следующая ждёт" if what == "pause" else "Продолжаю"
        if what == "stop":
            MD.resume()
            status, data = _call(manga_get.download_stop, settings, body={})
            return status == 200, data.get("error") or "Останавливаю…"
        if what == "skip":
            return (True, "Пропускаю галерею — качаю следующую") if MD.skip() else (False, "Сейчас ни одна галерея не качается")
        if what == "forget":
            return (True, "Скачивание с прошлого запуска не продолжится") if MD.forget_restore() else \
                (False, "Они уже продолжаются — остановить можно «Остановить»")
        if what == "drop":
            try:
                gone = MD.drop(int(number))
            except (TypeError, ValueError):
                gone = False
            return (True, "Убрано из очереди") if gone else (False, "Её уже нет в очереди")
        if what == "move":
            try:
                moved = MD.move(int(number), step)
            except (TypeError, ValueError):
                moved = False
            return (True, "Порядок поменялся") if moved else (False, "Дальше двигать некуда")
    if key == "video":                               # the other chat's: only its own API is called
        if what in ("pause", "resume"):
            status, data = _call(video_get.download_pause, settings, body={"pause": what == "pause"})
            return status == 200, data.get("error") or ("Пауза: файл остановится, где он есть" if what == "pause"
                                                          else "Продолжаю")
        if what == "stop":
            status, data = _call(video_get.download_stop, settings, body={})
            return status == 200, data.get("error") or "Останавливаю…"
        if what == "drop":
            status, data = _call(video_get.download_unqueue, settings, body={"id": number})
            return status == 200, data.get("error") or "Убрано из очереди"
    if key == "subs":
        job = J.current()
        if job is None or job.finished_at:
            return False, "Сейчас ничего не проверяется"
        if what == "pause":
            return (True, "Пауза: текущий файл докачается, следующий ждёт") if job.pause() else (False, "Не идёт")
        if what == "resume":
            job.resume()
            return True, "Продолжаю"
        if what == "stop":
            job.stop()
            return True, "Останавливаю: текущий файл докачается"
        if what == "skip":
            return (True, "Пропускаю — дальше следующая") if job.skip() else (False, "Пропустить нечего")
    return False, "Эта кнопка здесь ничего не делает"


def act_api(req: Request) -> Response:
    body = req.json()
    key, what = str(body.get("key") or ""), str(body.get("act") or "")
    try:
        step = int(body.get("step") or 0)
    except (TypeError, ValueError):
        step = 0
    ok, words = act(req.settings, key, what, body.get("id"), step)
    return Response.json({"ok": ok, "message": words, **({} if ok else {"error": words})}, 200 if ok else 409)


def all_api(req: Request) -> Response:
    """«Пауза всего» / «Продолжить всё» / «Остановить всё» (the queues too)."""
    what = str(req.json().get("act") or "")
    if what not in ("pause", "resume", "stop"):
        return Response.json({"ok": False, "error": "Непонятно, что сделать"}, 400)
    raw = raw_states(req.settings)
    done = []
    for row in summary(raw, sfw=_sfw())["jobs"]:
        if row["state"] == "restoring":
            continue
        if what == "pause" and row["pause"] or what == "resume" and row["resume"] or what == "stop" and row["stop"]:
            ok, _words = act(req.settings, row["key"], what)
            if ok:
                done.append(row["key"])
    if what == "stop":                               # the queues of kinds that do not go (one ending just now)
        from core import manhwa_queue as Q
        from webui.pages import video_get
        if Q.public() and "manhwa" not in done:
            Q.stop_all()
            done.append("manhwa")
        if video_get.queue_public() and "video" not in done:
            _call(video_get.download_stop, req.settings, body={})
            done.append("video")
    words = {"pause": "Всё на паузе", "resume": "Продолжаю", "stop": "Останавливаю всё и убираю очередь"}[what]
    return Response.json({"ok": True, "message": words if done else "Сейчас ничего не качается", "done": done})


def retry_api(req: Request) -> Response:
    """«Повторить не скачавшееся» of a line of «Готово»: exactly what did not come, into the queue."""
    from core import download_history
    entry = download_history.get(str(req.json().get("id") or ""))
    retry = entry.get("retry") if entry else None
    if not entry or not isinstance(retry, dict):
        return Response.json({"ok": False, "error": "Повторять нечего — этой строки уже нет"}, 409)
    try:
        words = _retry(req.settings, entry, retry)
    except _Refused as exc:
        return Response.json({"ok": False, "error": str(exc)}, 409)
    return Response.json({"ok": True, "message": words})


class _Refused(Exception):
    pass


def _retry(settings: dict, entry: dict, retry: dict) -> str:
    kind = entry.get("kind")
    if kind == "manhwa" and retry.get("chapters"):
        from core import manhwa_queue as Q
        chapters = [str(c) for c in retry["chapters"]]
        record = {"url": str(retry.get("url") or ""), "title": str(retry.get("title") or entry.get("title") or ""),
                  "branch": str(retry.get("branch") or ""), "where": str(retry.get("where") or ""),
                  "follow": bool(retry.get("follow", True)), "helpers": bool(retry.get("helpers", True)),
                  "chapters": chapters, "hentai": bool(entry.get("hentai")), "retry": True}
        if not record["url"].startswith(("http://", "https://")):
            raise _Refused("У этой строки нет адреса тайтла")
        waiting = Q.queued(record["url"], record["where"])
        if waiting is not None:                      # one download of a title: the chapters of both
            before = waiting["chapters"]
            record["chapters"] = None if before is None else list(dict.fromkeys([*before, *chapters]))
            record["retry"] = record["chapters"] is not None
        live = dict(settings)
        started = Q.start(f"Скачать: {record['title']}", lambda say, stop_check: Q.work(record, live, say, stop_check),
                          record=record, title=record["title"], count=len(record["chapters"] or chapters),
                          chosen=list(record["chapters"] or chapters))
        if started.get("queued"):
            return f"В очереди {started['queued']}-м: {plural(len(chapters), CHAPTERS)}"
        return f"Скачиваю снова: {plural(len(chapters), CHAPTERS)}"
    if kind == "manga" and retry.get("numbers"):
        from core import manga_download as MD, page_jobs
        from core import manga_library as M
        from webui.pages import manga_get
        numbers = [int(n) for n in retry["numbers"] if str(n).isdigit()][:MD.MAX_AT_ONCE]
        root = M.manga_root(settings)
        if not root or not Path(root).is_dir():
            raise _Refused("Папки манги нет на месте")
        places = MD.add(numbers)
        if places is not None:
            return f"В очереди: {plural(len(numbers), GALLERIES)}"
        try:
            page_jobs.start(manga_get.GET, manga_get._label(numbers), manga_get._run_of(numbers, Path(root)))
        except page_jobs.TaskBusy as exc:
            raise _Refused(f"{exc}. Дождитесь конца.") from exc
        return f"Скачиваю снова: {plural(len(numbers), GALLERIES)}"
    if kind in ("grab", "favorites") and isinstance(retry.get("sub"), dict):
        from core import subscription_jobs as J
        # the posts found before wait in the seed cache: no scan («given_posts» empty), only their download
        sub = dict(retry["sub"], given_posts={}, max_pages=1, run_mode="all", one_off=True)
        sub.pop("given_ids", None)
        sub["run_direction"] = sub.get("run_direction") or "newest_to_oldest"
        label = (f"{sub.get('name') or 'Избранное'}: докачать" if kind == "favorites"
                 else f"Галерея (booru): докачать — {sub.get('query') or ''}")
        try:
            if kind == "favorites":
                from core import favorites
                favorites.register()                  # its files go to the parser after it
            J.start(dict(settings), [sub], label=label, kind=kind, forget_seeds=True)
        except J.SubscriptionsBusy as exc:
            raise _Refused(f"{exc}. Когда это закончится, нажмите ещё раз.") from exc
        return "Докачиваю остальные"
    raise _Refused("Эту загрузку повторить нельзя")


def open_line(req: Request) -> Response:
    """«Открыть» of a line of «Готово»: the page of what it downloaded (the page has only the line's number)."""
    from core import download_history
    entry = download_history.get(req.arg("id")) or {}
    if not entry or (_sfw() and _entry_hidden(entry, req.settings)):
        return Response.redirect(PHONE_ADDRESS if req.path.startswith("/m/") else ADDRESS)
    link = _open_link(req.settings, entry) or ADDRESS
    if req.path.startswith("/m/"):
        from webui.pages import phone
        return Response.redirect(phone.twin(link.split("?")[0], link.split("?", 1)[1] if "?" in link else "")
                                 or PHONE_ADDRESS)
    return Response.redirect(link)


def _open_link(settings: dict, entry: dict) -> str:
    kind = entry.get("kind")
    if kind == "video":
        return f"/videos/s/{entry['series']}" if re.fullmatch(r"[0-9a-f]{12}", str(entry.get("series") or "")) else "/videos"
    if kind == "manhwa" and entry.get("folder"):
        from core import manhwa_queue as Q
        return Q.open_link(settings, str(entry["folder"]), str(entry.get("folder_kind") or "manhwa"))
    if kind == "manga":
        from core import manga_library as M
        root = M.manga_root(settings)
        folders = {str(Path(f)) for f in entry.get("folders") or []}
        found = [m for m in (M.LIBRARY.items(str(root)) if root else []) if str(Path(m.path)) in folders]
        return f"/manga/g/{found[0].id}" if len(found) == 1 else "/manga"
    return ""


# ── «Сама программа» ────────────────────────────────────────────────────────────────────────────────────────────────
def _followed(settings: dict) -> int:
    """How many titles the nightly round takes (their folders' ``source.json`` read — kept a minute)."""
    with _FOLLOWED_LOCK:
        if time.monotonic() - _FOLLOWED["at"] < FOLLOWED_TTL:
            return int(_FOLLOWED["n"])
    from core import manhwa_download as D
    try:
        n = sum(1 for _f, data in D.followed(settings) if data.get("follow", True))
    except OSError:
        n = 0
    with _FOLLOWED_LOCK:
        _FOLLOWED.update(at=time.monotonic(), n=n)
    return n


def _duty(key: str, name: str, about: str, *, setting: dict | None, state: str, status: str, part=None,
          action: dict | None = None) -> dict:
    return {"key": key, "name": name, "about": about, "setting": setting, "state": state, "status": status,
            "part": part, "action": action}


def _switch(settings: dict, key: str, default: bool = True) -> dict:
    return {"key": key, "kind": "bool", "value": bool(settings.get(DUTY_KEYS[key], default))}


def duties(settings: dict) -> list:
    """The rows of «Сама программа»."""
    from core import background_duties as B, download_history, manhwa_download as D, page_jobs
    reports = B.reports()
    now = time.time()
    sfw = _sfw()

    def ran(name: str) -> tuple:
        """(going now, the words of its last run that did something / its last run)."""
        entry = reports.get(name) or {}
        going = bool(entry.get("start")) and not entry.get("end")
        if entry.get("said"):
            return going, f"было {_when(entry['said_at'], now)} — {entry['said']}"
        if entry.get("end"):
            return going, f"было {_when(entry['end'], now)} — нового нет"
        return going, ""
    rows = []
    # the nightly round of new chapters
    on = bool(settings.get("manhwa_watch_auto", True))
    task = page_jobs.current(D.TASK)
    round_going = task is not None and task.running and task.label == B.ROUND_LABEL
    last = download_history.last_round()
    night_action = None                              # (v5.44.163) «Проверить сейчас» / «Продолжить сейчас»
    if round_going:
        meter = D.R.meter(since=10 ** 12, run=-1)[0]
        state, status = "running", "идёт сейчас" + (f" — «{meter['title']}»" if meter.get("title") and not sfw else "")
        if D.R.held():                               # it waits for the person to leave the computer
            status = "ждёт, пока вы отойдёте от компьютера, — чтобы не занимать процессор"
            night_action = {"act": "night", "label": "Продолжить сейчас"}
    elif not on:
        state, status = "off", "выключено — новые главы сами не скачиваются"
    elif B.waits("manhwa"):                          # its time has come: it begins when the person has left
        state, status = "idle", ("пора проверить — начнётся, когда вы отойдёте от компьютера (так проверка не "
                                 "занимает процессор, пока он вам нужен)")
        night_action = {"act": "night", "label": "Проверить сейчас"}
    else:
        night_action = {"act": "night", "label": "Проверить сейчас"}
        if last.get("at"):
            when, titles, got = last["at"], last.get("titles", 0), last.get("got", 0)
        else:
            try:
                kept = json.loads(D._watch_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                kept = {}
            when = float(kept.get("checked_at") or 0) if isinstance(kept, dict) else 0
            titles = int(kept.get("titles") or 0) if isinstance(kept, dict) else 0
            got = int(kept.get("got") or 0) if isinstance(kept, dict) else 0
        if when:
            extra = f", сайты не ответили: {last['problems']}" if last.get("problems") else ""
            state, status = "done", (f"было {_when(when, now)} — "
                                     f"{download_history.agree(titles, download_history.CHECKED_TITLES).lower()} "
                                     f"{plural(titles, TITLES)}, новых глав: {got}{extra}")
        else:
            state, status = "idle", "ещё не было — будет ночью (компьютер был выключен ночью — после запуска)"
    rows.append(_duty("night", "Новые главы — ночью",
                      f"Тайтлы с галочкой «Новые главы — скачивать самой» (сейчас {_followed(settings)}) проверяются "
                      "раз в сутки, ночью — или днём, когда вы отошли от компьютера.",
                      setting=_switch(settings, "night"), state=state, status=status, action=night_action))
    # the comics of the gallery to the manga folder
    from core import comic_manga
    on = bool(settings.get("comic_manga_auto", True))
    going, last_words = ran("comic_manga")
    check = page_jobs.current(comic_manga.TASK)
    if (check is not None and check.running) or going:
        state, status = "running", "идёт сейчас"
    elif not on:
        state, status = "off", "выключено"
    else:
        try:
            st = comic_manga.status(settings)
            moved = f"в папке манги комиксов из галереи — {st['comics']}, манги из папки парсера — {st['copies']}"
        except Exception:  # noqa: BLE001 - its file unreadable: the switch still works
            moved = ""
        state, status = ("done", last_words) if last_words else ("idle", moved or "ждёт своего круга")
    rows.append(_duty("comics", "Комиксы — в папку манги",
                      ("Комиксы галереи" if sfw else "Пулы e621 и сборки-комиксы") +   # («SFW»: no site of its world)
                      " от 15 страниц копируются в папку манги, их карточки — в корзину "
                      "«перенесено в мангу».", setting=_switch(settings, "comics"), state=state, status=status,
                      action={"act": "comics", "label": "Проверить сейчас"} if on else None))
    # «Из архива» by itself
    from core import manga_archive
    on = bool(settings.get("manga_archive_auto", True))
    going, last_words = ran("manga_archive")
    task = page_jobs.current(manga_archive.TASK)
    if task is not None and task.running:
        line = "" if sfw else _line(task)
        state, status = "running", "идёт сейчас" + (f" — {line}" if line else "")
    elif not on:
        state, status = "off", "выключено"
    else:
        state, status = ("done", last_words) if last_words else ("idle", "ждёт своего круга (раз в 10 минут)")
    rows.append(_duty("archive", "Узнавать мангу в архиве",
                      "Папки манги и манхвы без описания: тайтл ищется на сайтах по файлам и именам, по картинкам — не "
                      "больше 40 в день.", setting=_switch(settings, "archive"), state=state, status=status,
                      action={"href": "/get/archive", "label": "Открыть «Из архива»"}))
    # the sites asked about the cards from before
    from core import site_posts
    on = bool(settings.get("card_sets_ask_sites", True))
    going, last_words = ran("site_posts")
    try:
        st = site_posts.status(settings)
    except Exception:  # noqa: BLE001 - the base busy: without the counts
        st = {"waiting": 0, "known": 0}
    if not on:
        state, status = "off", "выключено"
    elif going:
        state, status = "running", f"идёт сейчас — ждут ответа сайтов: {st.get('waiting', 0)}"
    elif last_words:
        state, status = "done", last_words + (f"; ждут ответа: {st['waiting']}" if st.get("waiting") else "")
    else:
        state, status = "idle", f"сайты рассказали о постах: {st.get('known', 0)}" + (
            f"; ждут ответа: {st['waiting']}" if st.get("waiting") else "")
    rows.append(_duty("posts", "Спрашивать сайты о старых карточках",
                      "Кто загрузил пост и его родитель — из этого собираются сборки. Пока работает парсер — реже.",
                      setting=_switch(settings, "posts"), state=state, status=status))
    # the reduced copies → the originals
    from core import reduced_copies
    on = bool(settings.get("automatic_quality_upgrade_enabled", True))
    going, last_words = ran("originals")
    try:
        st = reduced_copies.status(settings)
    except Exception:  # noqa: BLE001 - the base busy: without the counts
        st = {"done": 0, "later": 0}
    if not on:
        state, status = "off", "выключено" + (f" — ждут: {st['later']}" if st.get("later") else "")
    elif going:
        state, status = "running", "идёт сейчас"
    else:
        state, status = ("done", last_words) if last_words else ("idle", f"получили оригинал: {st.get('done', 0)}")
    rows.append(_duty("originals", "Оригиналы вместо уменьшенных копий",
                      "Карточка из «sample_…» получает оригинал поста с сайта — если это та же картинка, только больше.",
                      setting=_switch(settings, "originals"), state=state, status=status))
    # the subscriptions' automatic check
    from core import subscription_jobs as J
    job = J.current()
    resting = J.auto_paused_until()
    if job is not None and not job.finished_at and job.kind == "auto":
        state, status = "running", f"идёт сейчас — подписка {job.index} из {len(job.subs)}"
    elif resting:
        state, status = "off", "отдыхает после «Остановить» до " + time.strftime("%H:%M", time.localtime(resting))
    elif job is not None and job.kind == "auto" and job.finished_at:
        state, status = "done", (f"было {_when(job.finished_at, now)} — "
                                 f"{download_history.agree(len(job.results), download_history.CHECKED).lower()} "
                                 f"{plural(len(job.results), SUBS)}, новых картинок: {job.downloaded}")
    else:
        going, last_words = ran("subscriptions")
        state, status = ("done", last_words) if last_words else ("idle", "проверяет раз в минуту те, чьё время пришло")
    rows.append(_duty("subs", "Автопроверка подписок",
                      "Подписки с «Авто» проверяются сами, каждая — раз в свой срок. Включается у каждой подписки.",
                      setting=None, state=state, status=status,
                      action=None if job is not None and not job.finished_at else
                      {"act": "subs", "label": "Проверить сейчас"}))
    # the trash
    days = int(settings.get("trash_auto_purge_days", 0) or 0)
    going, last_words = ran("lifecycle")
    if not days:
        state, status = "off", "только вручную — корзина сама не очищается"
    elif going:
        state, status = "running", "идёт сейчас"
    else:
        state, status = ("done", last_words) if last_words else ("idle", f"удаляет навсегда то, что лежит дольше "
                                                                         f"{days} дней")
    rows.append(_duty("trash", "Очистка корзины",
                      "Что лежит в корзине дольше срока, удаляется навсегда — раз в 5 минут, пока программа открыта.",
                      setting={"key": "trash", "kind": "choice", "value": days,
                               "choices": [[v, label] for v, label in TRASH_DAYS]},
                      state=state, status=status))
    return rows


def duties_api(req: Request) -> Response:
    return Response.json({"ok": True, "rows": duties(req.settings)})


def duty_api(req: Request) -> Response:
    """A switch of «Сама программа»: the same key as in «Настройки», saved the same way (``settings_store``)."""
    from webui import settings_store
    body = req.json()
    key = str(body.get("key") or "")
    setting = DUTY_KEYS.get(key)
    if setting is None or setting not in settings_store.PAGE_KEYS:
        return Response.json({"ok": False, "error": "Нет такого выключателя"}, 400)
    if key == "trash":
        try:
            value = int(body.get("value"))
        except (TypeError, ValueError):
            value = -1
        if value not in {v for v, _label in TRASH_DAYS}:
            return Response.json({"ok": False, "error": "Нет такого срока"}, 400)
    else:
        value = bool(body.get("value"))
    if not settings_store.save({setting: value}):
        return Response.json({"ok": False, "error": "Окно программы не ответило — не сохранено"}, 503)
    if setting == "manga_archive_auto" and value is False:
        from webui.pages import manga_archive        # switched off: a round of the background stops (as «Настройки»)
        manga_archive.stop_background()
    return Response.json({"ok": True, "message": "Сохранено"})


def duty_act_api(req: Request) -> Response:
    """«Проверить сейчас» of «Сама программа» — where it is there already: the comics (``comic_manga.check_now``), the
    subscriptions (every one switched on, as «Проверить все» of «Подписки»); (v5.44.163) the round of new chapters —
    begun now whoever is at the computer (``background_duties.start_round`` ``asked``), or, when it goes and waits
    for the person to leave, let go on («Продолжить сейчас»: ``manhwa_run.go_on``)."""
    key = str(req.json().get("key") or "")
    if key == "night":
        from core import background_duties as B, manhwa_download as D, manhwa_queue as Q, page_jobs
        task = page_jobs.current(D.TASK)
        if task is not None and task.running:
            if task.label != B.ROUND_LABEL:
                return Response.json({"ok": False, "error": "Сейчас идёт скачивание — дождитесь его конца"}, 409)
            if not D.R.held():
                return Response.json({"ok": False, "error": "Проверка уже идёт"}, 409)
            D.R.go_on()
            task._say("ПРОДОЛЖАЮ: вы сказали не ждать — новые главы проверяются сейчас")
            return Response.json({"ok": True, "message": "Продолжаю — проверка идёт"})
        if Q.waiting():
            return Response.json({"ok": False, "error": "В очереди есть скачивание — дождитесь его конца"}, 409)
        duties_now = B.current()
        live = duties_now._settings if duties_now is not None else (lambda settings=dict(req.settings): settings)
        try:
            B.start_round(live, asked=True)
        except page_jobs.TaskBusy:
            return Response.json({"ok": False, "error": "Проверка уже идёт"}, 409)
        return Response.json({"ok": True, "message": "Проверка новых глав идёт — её видно в «Загрузках»"})
    if key == "comics":
        from core import comic_manga, page_jobs
        try:
            comic_manga.check_now(req.settings)
        except comic_manga.ComicError as exc:
            return Response.json({"ok": False, "error": str(exc)}, 409)
        except page_jobs.TaskBusy:
            return Response.json({"ok": False, "error": "Проверка уже идёт"}, 409)
        return Response.json({"ok": True, "message": "Проверка идёт"})
    if key == "subs":
        from core import subscription_jobs as J
        from core.subscriptions import load_subscriptions
        subs = [s for s in load_subscriptions() if s.get("enabled", True)]
        if not subs:
            return Response.json({"ok": False, "error": "Нет включённых подписок"}, 409)
        try:
            J.start(dict(req.settings), subs, label="Все подписки")
        except J.SubscriptionsBusy as exc:
            return Response.json({"ok": False, "error": f"{exc}. Дождитесь конца или остановите."}, 409)
        return Response.json({"ok": True, "message": "Проверка подписок идёт"})
    return Response.json({"ok": False, "error": "Здесь нечего проверять"}, 400)


def link_line() -> str:
    """(v5.44.120) What stays of «Загрузки» on «По ссылке»: a line and the way to the section (its number — the badge
    of the header)."""
    return (f'<aside class="gl-side gl-side-link" aria-labelledby="gl-side-title">'
            f'<div class="gl-side-head"><h2 id="gl-side-title">Загрузки</h2></div>'
            f'<p class="gl-side-note">Всё, что качается, — манга, манхва, хентай, видео, «Галерея (booru)» и подписки: '
            f'что идёт, очередь, что пришло и что нет — в одном месте. «Скачать» на карточке ставит туда.</p>'
            f'<a class="gl-btn quiet" href="{ADDRESS}" data-dl-link>Открыть «Загрузки»</a></aside>')
