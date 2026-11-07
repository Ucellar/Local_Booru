"""(v5.44.125) The block «Манга в исходной папке» of the page «Парсер» (макет G1): what the folders of the parser that
look like a manga turned out to be — in «Манга» (confirmed), a сборка of the gallery (not a manga; «SauceNAO спрошу
позже» — its SauceNAO asked later; «пропарсится при следующем запуске» — its pages not in a run yet), waiting for the
sites of manga — and the owner's word on each («Это не манга» / «Это манга»).  The work itself —
``core/parser_manga.py``.

Its pictures (a folder's first page by its number) are asked by the row's key (``/api/parser/manga/cover``), never by
a path; the paths themselves are never on the page — the folders' names from the parser's folder only.
"""
from __future__ import annotations

import hashlib
import io
import time

from webui.html import esc
from webui.server import Request, Response

SHOWN = 7                       # rows shown before «Показать все N»
COUNTED = 30                    # rows whose cards of the gallery are counted (the base asked for each)
SWITCHES = ("parser_skip_manga", "parser_manga_fill")


def register(router) -> None:
    router.add("GET", r"/api/parser/manga", state)
    router.add("POST", r"/api/parser/manga/not", say_not)
    router.add("POST", r"/api/parser/manga/is", say_manga)
    router.add("POST", r"/api/parser/manga/switch", switch)
    router.add("GET", r"/api/parser/manga/cover", cover)


def _plural(n: int, forms: tuple) -> str:
    from webui.pages.post_sets import plural
    return plural(int(n), forms)


def _folder_set_id(key: str) -> str:
    """The id of a сборка of «Манга» of one folder's manga (``manga_works.sets``: «folder|<key>»)."""
    return hashlib.md5(("folder|" + key).encode("utf-8")).hexdigest()[:12]


def _when(stamp: int) -> str:
    if not stamp:
        return "позже"
    now = time.localtime()
    then = time.localtime(stamp)
    if then.tm_yday == now.tm_yday and then.tm_year == now.tm_year:
        return "сегодня в " + time.strftime("%H:%M", then)
    if stamp - time.time() < 2 * 86400 and then.tm_yday != now.tm_yday:
        return "завтра в " + time.strftime("%H:%M", then)
    return time.strftime("%d.%m в %H:%M", then)


def _kind(row: dict, live: bool) -> tuple:
    """(the class of the row's mark, its words)."""
    state = row["state"]
    works = row["works"]
    if state in ("manga", "part"):
        words = "Манга"
        if state == "part":
            words += f" — {sum(1 for w in works if w['state'] == 'manga')} из {len(works)}"
        return "manga", words
    if state == "not":
        nots = [w for w in works if w["state"] == "not"] or works
        if any(w.get("how") == "later" for w in nots):
            return "not", "Не манга — SauceNAO спрошу позже"
        if not all(w.get("handed") for w in nots):
            return "not", "Не манга — пропарсится при следующем запуске парсера"
        return "not", "Не манга — сборкой в галерее"
    if state == "wait":
        said = " ".join(w.get("by", "") for w in works if w["state"] == "wait").lower()
        hows = {w.get("how", "") for w in works if w["state"] == "wait"}
        return "wait", ("Ждёт очереди к сайтам манги" if "site" in hows else
                        "Ждёт: сайты манги не ответили" if "down" in hows else
                        "Ждёт SauceNAO (лимит)" if "лимит" in said or "хватит" in said else
                        "Ждёт SauceNAO" if "saucenao" in hows else "Ждёт: проверка не удалась")
    return ("going", "Проверяю…") if live else ("check", "Ещё не проверена")


def _wait_words(hows: list) -> str:
    """Why the waiting folders wait (the block's first line)."""
    words = [word for how, word in (("down", "сайты манги не ответили"), ("site", "очередь к сайтам манги"),
                                    ("saucenao", "SauceNAO"), ("", "проверка не удалась")) if how in hows]
    return " / ".join(words)


def _fill_line(work: dict) -> str:
    fill = work.get("fill") or {}
    state = fill.get("state")
    words = esc(str(fill.get("words") or ""))
    if state == "queued":
        return f'<p class="pm-sub">{words} — <b>докачиваю</b> (видно в «<a href="/get/downloads">Загрузках</a>»)</p>'
    if state in ("done", "failed", "none") and words:
        extra = f", не вышло {int(fill.get('failed') or 0)}" if fill.get("failed") else ""
        got = f" — докачано {int(fill.get('got') or 0)}" if fill.get("got") else ""
        return f'<p class="pm-sub">{words}{got}{extra}</p>'
    return ""


def _source_line(work: dict) -> str:
    site = work.get("site") or {}
    if work.get("url"):
        counts = site.get("counts") or {}
        tail = ""
        if counts.get("total"):
            tail = (" · все главы на месте" if not counts.get("missing") else
                    f" · в папке {counts.get('have', 0)} из {counts.get('total')}")
        if site.get("error"):
            tail = f" · сайт не ответил: {esc(str(site['error'])[:160])}"
        return f'<p class="pm-sub">описание, теги и главы — со страницы тайтла{tail}</p>'
    return ""


def _tools(row: dict, gallery: int) -> str:
    out = []
    state = row["state"]
    works = row["works"]
    if state in ("manga", "part"):
        pages = [w["page"] for w in works if w.get("page")]
        if row["set"] and len(pages) >= 2:
            out.append(f'<a class="button" href="/manga/s/{_folder_set_id(row["key"])}">Открыть сборку в «Манге»</a>')
        elif pages:
            out.append(f'<a class="button" href="{esc(pages[0])}">Открыть в «Манге»</a>')
        out.append(f'<button type="button" class="link" data-action="pm-not" data-key="{esc(row["key"])}">'
                   'Это не манга</button>')
    elif state == "not":
        if gallery:
            out.append(f'<a class="button" href="/post/{gallery}">Открыть сборку</a>')
        out.append(f'<button type="button" class="link" data-action="pm-is" data-key="{esc(row["key"])}">'
                   'Это манга</button>')
    else:
        out.append(f'<button type="button" class="link" data-action="pm-is" data-key="{esc(row["key"])}">'
                   'Это манга</button>')
        out.append(f'<button type="button" class="link" data-action="pm-not" data-key="{esc(row["key"])}">'
                   'Это не манга</button>')
    return "".join(out)


def _parts(row: dict, notes: list) -> str:
    """The manga of a сборка («silver soul»: «Сильвер Соул» — 13 частей …) and what the names were taken for."""
    items = []
    for n, work in enumerate(row["works"]):
        count = work.get("size", "")
        if work["parts"] > 1:
            parts_of = work.get("parts_of") or []
            first = parts_of[0]["name"] if parts_of else ""
            last = parts_of[-1]["name"] if parts_of else ""
            side = bool(parts_of) and all(str(p.get("label") or "").startswith("Глава ")
                                          and p.get("label") != p.get("name") for p in parts_of)
            folders = (f"папки «{esc(first)}» … «{esc(last)}» — одно название с номером в конце: одна манга, "
                       "части — главами" if side else          # (v5.44.125) a title's own chapter folders
                       f"папки «{esc(first)}» … «{esc(last)}» — "
                       + ("тома" if all(p.get("label", "").lower().startswith(("том", "vol")) for p in parts_of)
                          else "главы"))
        else:
            folders = f"папка «{esc(work.get('first', ''))}»"
        state = {"manga": "", "not": " · не манга", "wait": " · ждёт"}.get(work["state"], " · проверяю")
        items.append(f'<li data-state="{esc(work["state"] or "check")}"><img class="pm-mini" alt="" loading="lazy" '
                     f'decoding="async" src="/api/parser/manga/cover?k={esc(row["key"])}&amp;w={n}">'
                     f'<span class="pm-pn"><span class="pm-pt"><b>{esc(work["name"])}</b>'
                     f'<span class="pm-pc">{esc(count)}{state}</span></span>'
                     f'<span class="pm-pf">{folders}</span></span></li>')
    manga = sum(1 for w in row["works"] if w["state"] == "manga")
    head = (f"В «Манге» — сборка «{esc(row['name'])}»: {_plural(manga, ('манга', 'манги', 'манг'))} по порядку папок"
            if manga >= 2 else f"В папке — {_plural(len(row['works']), ('манга', 'манги', 'манг'))}")
    how = "".join(f"<li>{esc(text)}</li>" for text in notes)
    return (f'<details class="pm-open" data-key="{esc(row["key"])}"><summary>{head}</summary>'
            f'<ol class="pm-parts">{"".join(items)}</ol>' + (f'<ul class="pm-how">{how}</ul>' if how else "")
            + "</details>")


def _row(settings: dict, row: dict, live: bool, counted: bool, items: dict) -> str:
    from core import parser_manga as PM
    cls, words = _kind(row, live)
    works = row["works"]
    lines = []
    gallery = 0
    if row["state"] in ("manga", "part"):
        first = next((w for w in works if w["state"] == "manga"), works[0])
        tail = ""
        if row["set"]:
            folders = sum(w["parts"] for w in works)
            manga = sum(1 for w in works if w["state"] == "manga")
            tail = (f": <b>{_plural(folders, ('папка', 'папки', 'папок'))} → {_plural(manga, ('манга', 'манги', 'манг'))}"
                    + (f", одна сборка «{esc(row['name'])}»" if manga >= 2 else "") + "</b>")
        lines.append(f'<p><b>{esc(first.get("by", ""))}</b> → в «Мангу»{tail}</p>')
        lines.append(_source_line(first))
        for work in works:
            if work.get("error"):
                lines.append(f'<p class="pm-sub">«{esc(work["name"])}»: {esc(work["error"])}</p>')
            fill = _fill_line(work)
            if fill:
                lines.append(fill.replace('<p class="pm-sub">', f'<p class="pm-sub">«{esc(work["name"])}»: ', 1)
                             if row["set"] else fill)
        others = [w for w in works if w["state"] not in ("manga", "")]
        if row["set"] and others:
            lines.append('<p class="pm-sub">' + "; ".join(f"«{esc(w['name'])}» — {esc(w.get('by', ''))}"
                                                           for w in others[:3]) + "</p>")
    elif row["state"] == "not":
        nots = [w for w in works if w["state"] == "not"] or works
        later = next((w for w in nots if w.get("how") == "later"), None)
        handed = all(w.get("handed") for w in nots)
        by = works[0].get("by", "") if works else ""
        pictures = row.get("pages") or 0
        if later is not None:                           # SauceNAO not to be asked when it was checked: asked later
            lines.append(f'<p>{_plural(pictures, PM.PICTURES)}: <b>ни файлы, ни имя мангу не подтвердили</b> → на '
                         'booru-сайты, сборкой в галерее</p>')
            lines.append(f'<p class="pm-sub">не манга — SauceNAO спрошу {esc(_when(later.get("sauce_at", 0)))}: если '
                         'подтвердит мангу, перенесу в «Мангу» (её картинки из галереи — в корзину, их можно '
                         'вернуть)</p>')
        else:
            lines.append(f'<p>{_plural(pictures, PM.PICTURES)}: <b>{esc(by)}</b> → на booru-сайты, сборкой в '
                         'галерее</p>')
        if not handed:                                  # found «not a manga» by the background after the run
            if later is not None:                       # (else its mark says so)
                lines.append('<p class="pm-sub">пропарсится при следующем запуске парсера</p>')
        elif counted:
            found, brak, set_ids = 0, 0, []
            for work in works:
                one = PM._set_said(settings, _work_of(row, work["key"], items))
                found, brak = found + one[0], brak + one[1]
                if one[3]:
                    gallery = gallery or one[3]
                if one[2]:
                    set_ids.append(one[2])
            if found or brak:
                lines.append(f'<p class="pm-sub">найдено {found} · в Браке {brak}'
                             + (f" · одна сборка «{esc(row['name'])}»" if set_ids else
                                " · сборка соберётся после парсинга") + "</p>")
            else:
                lines.append('<p class="pm-sub">сборка соберётся после парсинга</p>')
    elif row["state"] == "wait":
        work = next((w for w in works if w["state"] == "wait"), works[0])
        lines.append(f'<p>{esc(work.get("by", ""))} — <b>проверю {esc(_when(work.get("later_at", 0)))}</b></p>')
        lines.append('<p class="pm-sub">пока — ни в «Мангу», ни на booru-сайты: без подтверждения ничего не '
                     'делается</p>')
    else:
        lines.append('<p class="pm-sub">' + ("ComicInfo.xml и ссылка в файлах, потом тайтл на сайтах манги по имени "
                                             "(и по названию пула e621), потом SauceNAO" if live else
                                             "проверю в следующем запуске парсера или в фоне") + "</p>")
        lines.append('<p class="pm-sub">пока — ни в «Мангу», ни на booru-сайты</p>')
    spin = '<span class="pm-spin" aria-hidden="true"></span>' if cls == "going" else ""
    stack = " stack" if row["set"] else ""
    extra = _parts(row, row.get("notes") or []) if row["set"] or any(w["parts"] > 1 for w in works) else ""
    return (f'<li class="pm-row" data-state="{esc(row["state"])}" data-key="{esc(row["key"])}">'
            f'<span class="pm-pic{stack}"><img alt="" loading="lazy" decoding="async" '
            f'src="/api/parser/manga/cover?k={esc(row["key"])}&amp;w=0"></span>'
            f'<div class="pm-what"><b>{esc(row["name"])}</b><span class="pm-path">{esc(row["where"])}</span>'
            f'<span class="pm-size">{esc(row["size"])}</span></div>'
            f'<div class="pm-state"><span class="pm-kind {cls}">{spin}{esc(words)}</span>{"".join(lines)}</div>'
            f'<div class="pm-tools">{_tools(row, gallery)}</div>{extra}</li>')


def _work_of(row: dict, key: str, items: dict):
    from core import manga_detect as MD
    item = items.get(row["key"]) or {}
    cand = MD.Candidate.of(item.get("cand") or {})
    return next((w for w in cand.works if w.key == key), cand.works[0] if cand.works else MD.Work(key="", name="",
                                                                                                 path=""))


def _live() -> bool:
    """The parser runs (its manga are checked beside the files) — the page asks again often."""
    from core.parser import jobs
    return jobs.is_running()


def body(settings: dict, *, wanted: str = "all", every: bool = False) -> str:
    """The block's inside (the page and ``/api/parser/manga`` give the same)."""
    from core import manga_detect as MD, parser_manga as PM
    data = PM.summary(settings)
    live = _live()
    rows = data["rows"]
    items = PM.STORE.items()                            # read once for the whole block
    for row in rows[:COUNTED]:                          # what the names were taken for (the сборки's notes)
        row["notes"] = []
        if not (row["set"] or any(w["parts"] > 1 for w in row["works"])):
            continue                                    # (only a сборка's or a manga of parts' row opens)
        item = items.get(row["key"]) or {}
        try:
            row["notes"] = PM.notes_of(MD.Candidate.of(item.get("cand") or {}))
        except OSError:
            row["notes"] = []
    groups = {"all": rows, "manga": [r for r in rows if r["state"] in ("manga", "part")],
              "not": [r for r in rows if r["state"] == "not"], "wait": [r for r in rows if r["state"] == "wait"],
              "check": [r for r in rows if r["state"] == "check"]}
    wanted = wanted if wanted in groups else "all"
    shown = groups[wanted] if every else groups[wanted][:SHOWN]
    skip = settings.get("parser_skip_manga", True)
    fill = settings.get("parser_manga_fill", True)
    left = int(data.get("left_today") or 0)
    if not rows:
        summary = ("Папок, похожих на мангу, пока нет: парсер смотрит на них при запуске, фон — раз в несколько минут."
                   if skip else "Выключено: парсер берёт все папки как обычно.")
    else:
        later = int(data.get("later") or 0)
        parts = [f'<span class="ok">{data["manga"]} — манга</span> (в «Манге»)',
                 f'<span class="no">{data["not"]} — не манга</span> (сборками в галерее'
                 + (f"; из них {later} — SauceNAO спрошу позже" + (", лимит на сегодня кончился" if left <= 0 else "")
                    + ": подтвердит мангу — перенесу в «Мангу»" if later else "") + ")"]
        if data["wait"]:
            why = _wait_words(data.get("waits") or [])
            parts.append(f'<span class="wait">{data["wait"]} — ждут</span>' + (f": {why}" if why else ""))
        if data["check"]:
            parts.append(f'<span class="going">{data["check"]} — {"проверяю" if live else "ещё не проверены"}</span>')
        summary = (f'<b>Найдено {_plural(data["total"], ("папка, похожая", "папки, похожих", "папок, похожих"))} на '
                   f'мангу:</b> ' + ", ".join(parts) + ".")
    chips = "".join(
        f'<button type="button" class="link{" active" if key == wanted else ""}" data-pm-filter="{key}">'
        f'{label} {len(groups[key])}</button>'
        for key, label in (("all", "все"), ("manga", "манга"), ("not", "не манга"), ("wait", "ждут"),
                           ("check", "проверяю" if live else "не проверены"))
        if key == "all" or groups[key])
    listed = "".join(_row(settings, row, live, n < COUNTED, items) for n, row in enumerate(shown))
    more = ""
    if not every and len(groups[wanted]) > SHOWN:
        rest = groups[wanted][SHOWN:]
        tally = []
        for key, word in (("manga", "манга"), ("not", "не манга"), ("wait", "ждут"),
                          ("check", "ещё проверяю" if live else "ещё не проверены")):
            n = sum(1 for r in rest if (r["state"] in ("manga", "part")) == (key == "manga") and
                    (key == "manga" or r["state"] == key))
            if n:
                tally.append(f"{n} — {word}")
        more = (f'<p class="pm-more"><button type="button" class="link" data-action="pm-all">Показать все '
                f'{len(groups[wanted])}</button> — ещё {len(rest)}' + (": " + ", ".join(tally) if tally else "")
                + "</p>")
    switches = f"""<div class="pm-switches">
    <label class="pm-sw"><input type="checkbox" class="switch" data-pm-switch="parser_skip_manga"{" checked" if skip else ""}>
      <span><b>Мангу из исходной папки — проверять по сайтам манги и SauceNAO и класть в «Мангу»</b>
      <small>сначала ComicInfo.xml и ссылка в файлах, потом тайтл на сайтах манги — по имени папки и названию пула e621
      (если её страницы есть на e621), страницы сверяются с тайтлом; SauceNAO — только если это ничего не дало: 1–2
      страницы на мангу, вместе с «Из архива» — не больше {int(data.get("per_day") or 0)} поисков в день, сегодня
      осталось {left}. Выключено — парсер берёт все папки как обычно</small></span></label>
    <label class="pm-sw"><input type="checkbox" class="switch" data-pm-switch="parser_manga_fill"{" checked" if fill else ""}>
      <span><b>Докачивать недостающие главы</b>
      <small>главы, которых нет в папке, — с сайта манги, который её подтвердил (видно в «Загрузках»); платные —
      никогда</small></span></label>
  </div>"""
    return f"""<div class="pm-head"><h2 id="pm-h">Манга в исходной папке</h2></div>
  <p class="pm-sum">{summary}</p>
  {switches}
  {f'<nav class="pm-filter" aria-label="Какие папки показать">Показать: {chips}</nav>' if rows else ""}
  {f'<ol class="pm-rows">{listed}</ol>' if shown else ""}
  {more}
  <p class="pm-foot">В «Мангу» попадает только подтверждённое сайтом манги: <b>ComicInfo.xml, ссылка в файлах или тайтл
    на сайте манги, чьи страницы совпали со страницами папки</b> (по имени папки, названию пула e621 или ответу
    SauceNAO). Пул e621 сам по себе мангу не подтверждает. Не подтвердилось — не манга: страницы идут на booru-сайты, как
    обычно, и сборкой в галерею. Пока сайты манги не отвечают — папка ждёт; кончился лимит SauceNAO — папка парсится как
    обычно, а SauceNAO спросится позже: подтвердит мангу — перенесу в «Мангу». Файлы исходной папки не трогаются — в
    «Манге» копия. Платные главы — никогда.</p>
  <span class="action-status" role="status"></span>"""


def block(settings: dict) -> str:
    """The section on the page «Парсер»."""
    return (f'<section class="panel pm" id="parser-manga" aria-labelledby="pm-h" data-pm-url="/api/parser/manga" '
            f'data-live="{"1" if _live() else ""}"><div class="pm-body">{body(settings)}</div></section>')


# ── the API ────────────────────────────────────────────────────────────────────────────────────────────────────────
def state(req: Request) -> Response:
    from core import parser_manga as PM
    wanted = req.arg("state") or "all"
    every = req.arg("all") == "1"
    data = PM.summary(req.settings)
    rows = [{k: v for k, v in r.items() if k in ("key", "name", "where", "set", "state", "size", "pages")} |
            {"works": [{k: v for k, v in w.items() if k in ("key", "name", "state", "how", "by", "size", "page",
                                                              "later_at", "fill", "sauce_at", "handed")}
                       for w in r["works"]]}
            for r in data["rows"]]
    return Response.json({"ok": True, "html": body(req.settings, wanted=wanted, every=every), "live": _live(),
                          "rev": PM.STORE.rev, "rows": rows,
                          "counts": {k: data[k] for k in ("total", "manga", "not", "wait", "check", "later",
                                                          "left_today")}})


def _key(req: Request) -> str:
    return str((req.json() or {}).get("key") or "")[:64]


def say_not(req: Request) -> Response:
    from core import parser_manga as PM
    got = PM.say_not(req.settings, _key(req))
    return Response.json(got, 200 if got.get("ok") else 404)


def say_manga(req: Request) -> Response:
    from core import parser_manga as PM
    got = PM.say_manga(req.settings, _key(req))
    return Response.json(got, 200 if got.get("ok") else 404)


def switch(req: Request) -> Response:
    """The block's switches: «Мангу из исходной папки — …» and «Докачивать недостающие главы» (the settings)."""
    from webui import settings_store
    body_ = req.json() or {}
    name = str(body_.get("name") or "")
    if name not in SWITCHES:
        return Response.json({"ok": False, "error": "Такого переключателя нет"}, 400)
    settings_store.save({name: bool(body_.get("value"))})
    return Response.json({"ok": True})


def cover(req: Request) -> Response:
    """A row's first page, small (by the row's key and the manga's place in it — never a path)."""
    from core import parser_manga as PM
    try:
        index = int(req.arg("w") or 0)
    except ValueError:
        index = 0
    picture = PM.cover_of(req.arg("k")[:64], index)
    if picture is None:
        return Response.not_found("Картинки нет")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(picture.read_bytes())) as im:
            im = im.convert("RGB")
            width, height = im.size
            if height > 2 * width:                   # a strip: its top
                im = im.crop((0, 0, width, int(width * 1.5)))
            im.thumbnail((160, 240))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
    except (OSError, ValueError) as exc:
        return Response.not_found(f"Картинка не прочиталась: {type(exc).__name__}")
    return Response(200, out.getvalue(), "image/jpeg", {"Cache-Control": "private, max-age=600"})
