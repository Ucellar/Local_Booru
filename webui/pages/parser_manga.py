"""(v5.44.125) The block «Манга в исходной папке» of the page «Парсер» (макет G1): what the folders of the parser that
look like a manga turned out to be — in «Манга» (confirmed), a сборка of the gallery (not a manga; «SauceNAO спрошу
позже» — its SauceNAO asked later; «пропарсится при следующем запуске» — its pages not in a run yet), waiting for the
sites of manga — and the owner's word on each («Это не манга» / «Это манга»).  The work itself —
``core/parser_manga.py``.

Its pictures (a folder's first page by its number) are asked by the row's key (``/api/parser/manga/cover``), never by
a path; the paths themselves are never on the page — the folders' names from the parser's folder only.

(v5.44.160; the owner: «парсинг манги через парсер работает хуй пойми как»; the mockup B approved 11.11.2026) The
block is the tab «Манга из папки» of the page «Парсер», and it says what happens to a folder as three steps: on top —
how it works (a folder that looks like a manga → compared with the sites of manga → a copy in «Манга» or the gallery
as every picture), the counts of the folders are the filters, and every folder is a track of three points — «Найдена»
(what it looked like a manga by), what the check said (and why, in one line) and where the folder went.  Every word
and button of the block before is here; the long explanation is behind «Как программа решает, что это манга».

(v5.44.166; the owner: «нужна возможность перепроверить мангу которая через парсер там сейчас это не манга и открыть в
манге а нужно что бы была возможность перепроверить») Every row has «Перепроверить» (``core/parser_manga_again.py``:
the folder is looked at anew — its files, its name on the sites of manga, SauceNAO), the rows «не манга» —
«Перепроверить все» over the list (asked twice; the files and the names alone); a row being looked at again says so,
and what the look said stays under what the check says.

(v5.44.169; the owner: «почему нет возможности просто взять и вставить ссылку на мангу если она не подтвердилась?»)
Every folder's row has «Указать ссылку»: a line under the row with a field for the link of the manga's page (a title
of a site of manga, a gallery of nhentai) — ``core/parser_manga_again.say_link``; a row of several manga — at each
of them.  A row nhentai confirmed says how many of the gallery's pages the folder has, and «Докачать» leads to the
gallery's card when its pages can be added to the copy.
"""
from __future__ import annotations

import hashlib
import io
import logging
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
    router.add("POST", r"/api/parser/manga/now", download_now)
    router.add("POST", r"/api/parser/manga/again", look_again)
    router.add("POST", r"/api/parser/manga/link", link)
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


def _ago(stamp: int) -> str:
    """When something was done: «сегодня в 14:05», «11.11 в 09:30»."""
    then = time.localtime(int(stamp or 0))
    now = time.localtime()
    if then.tm_yday == now.tm_yday and then.tm_year == now.tm_year:
        return "сегодня в " + time.strftime("%H:%M", then)
    return time.strftime("%d.%m в %H:%M", then)


def _again_later(row: dict) -> int:
    """(v5.44.166) A row marked to be looked at again whose look is put off: when it goes on (0 — it goes now)."""
    marks = [int(w.get("again_at") or 0) for w in row["works"] if w.get("again")]
    return min(marks) if marks and min(marks) > time.time() else 0


def _fast(rows: list) -> bool:
    """(v5.44.166) A look asked for by «Перепроверить» is pending: poll as often as while the parser
    runs (a look put off to later — the sites' pace, no answer — is asked about as seldom as ever)."""
    return any(r.get("again") and not r.get("again_auto") and not _again_later(r)
               # (v5.44.170) …and while the pages a gallery's copy misses are being downloaded: its row counts them
               or any((w.get("site") or {}).get("going") or w.get("eh_photo") and
                      (w.get("fill") or {}).get("state") == "queued" for w in r.get("works") or []) for r in rows)


def _again_button(row: dict) -> str:
    """(v5.44.166) «Перепроверить» of a row (none while it is being looked at again; (v5.44.169) a row the program
    looks at again by itself, in its queue, keeps it: pressed — it goes first)."""
    if row.get("again") and not row.get("again_auto"):
        return ""
    return (f'<button type="button" class="link" data-action="pm-again" data-key="{esc(row["key"])}" '
            'title="Заново сверить с сайтами манги: файлы папки, её название (и на сайтах, которые пускают только '
            'браузер: com-x.life, Nude-Moon, MangaDex), SauceNAO">Перепроверить</button>')


def _link_button(row: dict, work: "dict | None" = None) -> str:
    """(v5.44.169) «Указать ссылку» of a row (of one manga of a row of several — ``work``): it opens the row's line
    with the field (none while the row is being looked at)."""
    if row.get("again") and not row.get("again_auto"):
        return ""
    named = (f' data-work="{esc(work["key"])}" data-work-name="{esc(work["name"])}"' if work is not None else "")
    return (f'<button type="button" class="link" data-action="pm-link" data-key="{esc(row["key"])}"{named} '
            'title="Вставить ссылку на страницу этой манги: программа возьмёт с неё описание, теги и главы">'
            'Указать ссылку</button>')


def _link_form(row: dict) -> str:
    """(v5.44.169) The line under a row where the link of its manga is pasted (hidden until «Указать ссылку»)."""
    from core import parser_manga_again as PA
    if row.get("again") and not row.get("again_auto"):
        return ""
    key = esc(row["key"])
    return (f'<form class="pm-link" data-key="{key}" hidden>'
            f'<label for="pm-link-{key}">Ссылка на страницу этой манги</label>'
            f'<span class="pm-link-row"><input type="url" id="pm-link-{key}" name="url" maxlength="2000" required '
            'autocomplete="off" spellcheck="false" placeholder="https://… — страница тайтла на сайте манги или '
            'галерея nhentai">'
            '<button type="submit" class="button">Привязать</button>'
            '<button type="button" class="link" data-action="pm-link-close">Отмена</button></span>'
            f'<span class="pm-sub">Подходит страница тайтла на {esc(PA.link_words())}. Программа откроет её, '
            'скопирует папку в «Мангу», возьмёт со страницы описание и теги и докачает главы, которых нет в папке. '
            'Исходная папка не трогается.</span>'
            '<span class="action-status" role="status"></span></form>')


def _again_said(row: dict) -> str:
    """(v5.44.166) What the last second look said of the row's manga, under what its check says."""
    lines = []
    for work in row["works"]:
        said = work.get("again_said") or {}
        if not said.get("at"):
            continue
        name = f"«{esc(work['name'])}»: " if len(row["works"]) > 1 else ""
        when = esc(_ago(said["at"]))
        if said.get("refused"):                         # (v5.44.169) the owner's link not taken
            words = f"ссылка не подошла ({when}): {esc(str(said['refused']))}"
        elif said.get("hand_kept"):
            words = (f"перепроверка {when} назвала другой тайтл («{esc(str(said.get('other') or ''))}»), но ссылку "
                     "указали вы — оставлена ваша")
        elif said.get("link") and said.get("moved"):
            words = (f"привязана по вашей ссылке {when} (был тайтл «{esc(str(said.get('was_title') or ''))}») — "
                     "описание, теги и главы теперь с этой страницы")
        elif said.get("link"):
            words = f"привязана по вашей ссылке {when}"
        elif said.get("kept"):
            words = (f"перепроверка {when} её не подтвердила ({esc(str(said.get('by') or ''))}) — оставлена как "
                     "была: в «Манге». Если это не манга, нажмите «Это не манга»")
        elif said.get("moved"):
            words = (f"перепроверено {when}: тайтл теперь другой (был «{esc(str(said.get('was_title') or ''))}») — "
                     "описание, теги и главы с него")
        elif said.get("state") == "manga" and said.get("was") == "manga":
            words = f"перепроверено {when}: подтвердилась снова"
        elif said.get("state") == "manga":
            words = f"перепроверено {when}: подтвердилась"
        else:
            words = f"перепроверено {when}: не подтвердилась"
        lines.append(f'<span class="pm-sub">{name}{words}</span>')
    return "".join(lines[:3])


def _kind(row: dict, live: bool, *, during_run: bool = False) -> tuple:
    """(the class of the row's mark, its words)."""
    state = row["state"]
    works = row["works"]
    if row.get("again"):                                # (v5.44.166) «Перепроверить» pressed
        linked = any(w.get("again_link") for w in works)
        if _again_later(row):
            return "wait", "Открою ссылку позже" if linked else "Перепроверю позже"
        if live and not during_run:
            return "wait", "Ждёт окончания парсера"
        # The saved request proves that a check is pending, not that this particular row is running.
        return "wait", "Ссылка в очереди на проверку" if linked else "В очереди на перепроверку"
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
        return "wait", ("Ждёт чтения папки" if "files" in hows else
                        "Ждёт очереди к сайтам манги" if "site" in hows else
                        "Ждёт: сайты манги не ответили" if "down" in hows else
                        "Ждёт SauceNAO (лимит)" if "лимит" in said or "хватит" in said else
                        "Ждёт SauceNAO" if "saucenao" in hows else "Ждёт: проверка не удалась")
    return ("wait", "В очереди на проверку") if during_run else ("check", "Ещё не проверена")


def _wait_words(hows: list) -> str:
    """Why the waiting folders wait (the block's first line)."""
    words = [word for how, word in (("files", "не удалось прочитать папку"),
                                    ("down", "сайты манги не ответили"), ("site", "очередь к сайтам манги"),
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
        if site.get("gallery"):                         # (v5.44.169) a gallery of nhentai: its pages, not chapters
            if counts.get("total"):
                tail = (" · все страницы на месте" if not counts.get("missing") else
                        f" · в папке {counts.get('have', 0)} стр. из {counts.get('total')}")
                if counts.get("missing") and site.get("going"):        # (v5.44.170) downloaded by themselves
                    tail += (' — <b>докачиваю недостающие</b> (видно в «<a href="/get/downloads">Загрузках'
                             '</a>»)')
            if site.get("error"):
                tail = f" · nhentai не ответил: {esc(str(site['error'])[:160])}"
            return f'<p class="pm-sub">описание и теги — со страницы галереи nhentai{tail}</p>'
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
    one = _link_button(row) if len(works) == 1 else ""  # (v5.44.169) a row of several manga — at each of them
    if state in ("manga", "part"):
        pages = [w["page"] for w in works if w.get("page")]
        if row["set"] and len(pages) >= 2:
            out.append(f'<a class="button" href="/manga/s/{_folder_set_id(row["key"])}">Открыть сборку в «Манге»</a>')
        elif pages:
            out.append(f'<a class="button" href="{esc(pages[0])}">Открыть в «Манге»</a>')
        for work in works:                              # (v5.44.169) the gallery has pages the copy has not
            site = work.get("site") or {}
            missing = int((site.get("counts") or {}).get("missing") or 0)
            if site.get("gallery") and site.get("fill") and missing and work.get("url") and not site.get("going"):
                from urllib.parse import quote
                out.append(f'<a class="button" href="/get?url={quote(str(work["url"]), safe="")}&amp;go=1" '
                           'title="Откроется карточка галереи: «Докачать» скачает в эту же папку только страницы, '
                           f'которых в ней нет">Докачать {_plural(missing, ("страницу", "страницы", "страниц"))}</a>')
                break
        out.append(_again_button(row))
        out.append(one)
        out.append(f'<button type="button" class="link" data-action="pm-not" data-key="{esc(row["key"])}">'
                   'Это не манга</button>')
    elif state == "not":
        if gallery:
            out.append(f'<a class="button" href="/post/{gallery}">Открыть сборку</a>')
        out.append(_again_button(row))
        out.append(one)
        out.append(f'<button type="button" class="link" data-action="pm-is" data-key="{esc(row["key"])}">'
                   'Это манга</button>')
    elif state == "wait":
        out.append(_again_button(row))
        out.append(one)
        out.append(f'<button type="button" class="link" data-action="pm-is" data-key="{esc(row["key"])}">'
                   'Это манга</button>')
        out.append(f'<button type="button" class="link" data-action="pm-not" data-key="{esc(row["key"])}">'
                   'Это не манга</button>')
    else:
        out.append(one)
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
        own = _link_button(row, work) if len(row["works"]) > 1 else ""      # (v5.44.169)
        items.append(f'<li data-state="{esc(work["state"] or "check")}"><img class="pm-mini" alt="" loading="lazy" '
                     f'decoding="async" src="/api/parser/manga/cover?k={esc(row["key"])}&amp;w={n}">'
                     f'<span class="pm-pn"><span class="pm-pt"><b>{esc(work["name"])}</b>'
                     f'<span class="pm-pc">{esc(count)}{state}</span>{own}</span>'
                     f'<span class="pm-pf">{folders}</span></span></li>')
    manga = sum(1 for w in row["works"] if w["state"] == "manga")
    head = (f"В «Манге» — сборка «{esc(row['name'])}»: {_plural(manga, ('манга', 'манги', 'манг'))} по порядку папок"
            if manga >= 2 else f"В папке — {_plural(len(row['works']), ('манга', 'манги', 'манг'))}")
    how = "".join(f"<li>{esc(text)}</li>" for text in notes)
    return (f'<details class="pm-open" data-key="{esc(row["key"])}"><summary>{head}</summary>'
            f'<ol class="pm-parts">{"".join(items)}</ol>' + (f'<ul class="pm-how">{how}</ul>' if how else "")
            + "</details>")


def _found_by(row: dict) -> str:
    """What the folder looked like a manga by (the first point of its track), from what its parts are."""
    works = row["works"]
    if row["set"]:
        return _plural(sum(w["parts"] for w in works), ("папка", "папки", "папок")) + " с одним именем"
    many = next((w for w in works if w["parts"] > 1), None)
    if many is not None:
        parts_of = many.get("parts_of") or []
        if parts_of:
            return f"папки «{parts_of[0]['name']}» … «{parts_of[-1]['name']}»"
        return _plural(many["parts"], ("часть", "части", "частей")) + " по порядку"
    return "страницы по порядку"


def _node(cls: str, title: str, lines: list, *, kind: str = "") -> str:
    """A point of a folder's track: its state (done, good, now, no, todo), its word and its lines."""
    head = f'<b class="pm-kind {kind}">{title}</b>' if kind else f"<b>{title}</b>"
    return f'<div class="mx-node {cls}">{head}{"".join(line for line in lines if line)}</div>'


def _eh_photo_status(work: dict) -> tuple:
    """A photo gallery uses the manga card, while its source pictures stay where they were."""
    fill = work.get("fill") or {}
    state = fill.get("state")
    if state == "queued":
        tone, title, words = "now", "Скачивается в «Мангу»", "Галерея скачивается в «Мангу»"
    elif work.get("page"):
        tone, title, words = "good", "В «Манге»", "Галерея сохранена в «Манге»"
    elif state == "held":
        tone, title, words = "todo", "Ждёт скачивания в «Мангу»", "Скачивание начнётся, когда вы отойдёте"
    elif state == "off":
        tone, title, words = "todo", "Не скачивается", "Автоматическое скачивание в «Мангу» выключено"
    elif state in ("failed", "again"):
        tone, title, words = "no", "Не скачалось", "Галерея пока не скачалась в «Мангу»"
    else:
        tone, title, words = "todo", "В очереди в «Мангу»", "Галерея ожидает скачивания в «Мангу»"
    text = f'<span>{words}; исходные картинки остаются на месте</span>'
    if state in ("queued", "failed", "again"):
        text += '<p class="pm-sub">Подробнее — в «<a href="/get/downloads">Загрузках</a>»</p>'
    if fill.get("words"):
        text += f'<p class="pm-sub">{esc(str(fill["words"]))}</p>'
    return tone, title, text


def _loose_row(row: dict, live: bool = False) -> str:
    """(v5.44.164) A manga found among the loose pictures (``core/parser_loose.py``) — the same track of three
    points: «Найдена» — SauceNAO named it for so many pictures; what the sites of manga said (a page of the chapter
    alike, or not); where the pictures went (the title downloaded into «Манга» and they out of Брак — or Брак as it
    was).  Its pictures — by their files' names, never a path."""
    from core import parser_manga as PM
    work = row["works"][0]
    state = row["state"]
    count = int(row.get("pages") or 0)
    best = float(work.get("sim") or 0)
    named = esc(row.get("said") or row["name"])
    gallery = bool(row.get("gallery"))                  # (v5.44.170) a page of a gallery of nhentai: its own words
    gallery_label = str(row.get("gallery_label") or "nhentai")
    it, its, where = (("галерея", "галереи", "на " + esc(gallery_label)) if gallery else
                      ("тайтл", "тайтла", "на сайтах манги"))
    found = (f"<span>SauceNAO: страница {'галереи' if gallery else 'манги'} «{named}» ({best:.0f} %)</span>"
             if count == 1 else
             f"<span>SauceNAO назвал {'галерею' if gallery else 'мангу'} «{named}» у "
             f"{_plural(count, ('картинки', 'картинок', 'картинок'))} (до {best:.0f} %)</span>")
    check, went = [], []
    went_cls, went_title = "todo", "Пока в «Браке»"
    if state == "manga":
        cls, check_cls, check_title = "manga", "done", "Подтверждена"
        check.append(f"<span>{esc(work.get('by', ''))}</span>")
        others = count - int(row.get("accepted") or 0)
        if others > 0:
            check.append(f'<span class="pm-sub">не её страницы: {others} — '
                         + ("остаётся" if others == 1 else "остаются") + " в «Браке»</span>")
        fill = work.get("fill") or {}
        trashed = int(work.get("trashed") or 0)
        loads = '«<a href="/get/downloads">Загрузках</a>»'
        if work.get("eh_photo"):
            went_cls, went_title, text = _eh_photo_status(work)
            went.append(text)
        elif row.get("placed"):
            went_cls, went_title = "good", "В «Манге»"
            went.append("<span>" + (f"{_plural(trashed, PM.PICTURES)} из «Брака» — в корзине («перенесено в мангу»), "
                                    "их можно вернуть" if trashed else f"{it} в «Манге»; её картинок в «Браке» нет")
                        + "</span>")
            if fill.get("state") == "done":
                went.append(f'<p class="pm-sub">{"страницы" if gallery else "главы"} скачиваются с сайта — видно в '
                            f'{loads}</p>')
        elif fill.get("state") == "queued":
            went_cls, went_title = "now", "Скачивается"
            went.append(f"<span>{it} — в {loads}; картинки уйдут из «Брака», когда "
                        f"{'она появится' if gallery else 'он появится'} в «Манге»</span>")
        elif fill.get("state") == "held":
            went_title = "Ждёт, пока вы отойдёте"
            went.append("<span>скачивание начнётся, когда за компьютером никого не будет, — чтобы не занимать "
                        "процессор, пока он вам нужен</span>")
        elif fill.get("state") == "off":
            went_title = "Не скачивается"
            went.append("<span>выключено «Докачивать главы, которых нет в папке» — картинки остаются в «Браке»</span>")
        elif fill.get("state") == "again":
            went_cls, went_title = "no", "Не скачалось"
            went.append(f"<span>попробую ещё раз {esc(_when(int(fill.get('at') or 0)))}; что пошло не так — в {loads}, "
                        "вкладка «Готово»</span>")
        elif fill.get("state") == "failed":
            went_cls, went_title = "no", "Не скачалось"
            went.append(f"<span>несколько попыток не удались (видно в {loads}, вкладка «Готово») — картинки остаются "
                        "в «Браке»</span>")
        else:
            went_title = "Сейчас скачаю"
            went.append(f"<span>скачивание встанет в очередь {loads} в ближайшие минуты</span>")
    elif state == "not":
        cls, check_cls = "not", "no"
        check_title = "Не манга" if row.get("hand") == "not" else "Не подтвердилась"
        check.append(f"<span>{esc(work.get('by', ''))}</span>")
        went_cls, went_title = "done", "В «Браке»"
        went.append("<span>" + ("картинка остаётся" if count == 1 else "картинки остаются")
                    + " в «Браке», как " + ("была" if count == 1 else "были") + "</span>")
    elif state == "wait":
        cls, check_cls = "wait", "now"
        check_title = (f"Ждёт: {gallery_label} не ответил" if gallery else
                       "Ждёт очереди к сайтам манги" if work.get("how") == "site" else
                       "Ждёт: сайты манги не ответили")
        check.append(f'<span>{esc(work.get("by", ""))} — <b>проверю {esc(_when(work.get("later_at", 0)))}</b></span>')
        went.append("<span>без подтверждения ничего не делается</span>")
    else:
        cls, check_cls, check_title = "check", "todo", "Ещё не проверена"
        check.append(f"<span>сверю со страницами {its} {where} — в фоне, раз в несколько минут</span>")
        went.append("<span>без подтверждения ничего не делается</span>")
    if row.get("again"):
        cls, check_title = _kind(row, live)
        check_cls = "todo"
        later = _again_later(row)
        why = str(work.get("again_why") or work.get("by") or "")
        if later:
            check = [f'<span>перепроверю {esc(_when(later))}' + (f": {esc(why)}" if why else "") + "</span>"]
        else:
            waiting = "после завершения основного парсера" if live else "по очереди"
            check = [f"<span>перепроверка запрошена: {waiting} повторю поиск {'галереи' if gallery else 'тайтла'} "
                     f"{where} и сверку картинок со страницами {'галереи' if gallery else 'главы'}</span>"]
            if state in ("not", "wait", "manga") and work.get("by"):
                check.append(f'<span class="pm-sub">Предыдущая проверка: {esc(str(work["by"]))}</span>')
    tools = []
    key = esc(row["key"])
    if state == "manga" and work.get("page"):
        tools.append(f'<a class="button" href="{esc(work["page"])}">Открыть в «Манге»</a>')
    if state == "manga" and not row.get("placed") and (work.get("fill") or {}).get("state") in ("held", "again",
                                                                                                 "failed"):
        tools.append(f'<button type="button" class="button" data-action="pm-now" data-key="{key}">Скачать сейчас'
                     "</button>")
    if state in ("not", "wait") and not row.get("again"):   # (v5.44.166) its name, its pictures against the sites anew
        tools.append(f'<button type="button" class="link" data-action="pm-again" data-key="{key}" title="Заново '
                     f'найти {"галерею" if gallery else "тайтл"} {where} и сверить картинки со страницами '
                     f'{"галереи" if gallery else "главы"}">Перепроверить</button>')
    if state == "not":
        if row.get("can_say"):
            tools.append(f'<button type="button" class="link" data-action="pm-is" data-key="{key}" title="Скачать '
                         f'{"галерею" if gallery else "тайтл"} «{esc(row["name"])}» в «Мангу»">Это манга</button>')
    else:
        tools.append(f'<button type="button" class="link" data-action="pm-not" data-key="{key}">Это не манга</button>')
    tools.append(_link_button(row))
    listed = []
    for one in row.get("files") or []:
        if one["state"] == "manga":
            what = (one.get("page") or ("страница этой манги — SauceNAO уверен (" + f"{float(one.get('sim') or 0):.0f} %)"
                                        if one.get("how") == "sure" else "вы сказали: «Это манга»"))
            what += " · в корзине" if one.get("trashed") else ""
        elif one["state"] == "not":
            what = one.get("why") or "не её страница"
        else:
            what = (f"глава {one['chapter']} — " if one.get("chapter") else "") + "ещё не сверена"
        listed.append(f"<li>«{esc(one['name'])}» — {esc(what)}</li>")
    more = count - len(listed)
    files = (f'<details class="pm-open" data-key="{key}"><summary>{_plural(count, PM.PICTURES)}'
             + (f" — показаны первые {len(listed)}" if more > 0 else "") + "</summary>"
             f'<ul class="pm-how">{"".join(listed)}</ul></details>') if listed else ""
    spin = '<span class="pm-spin" aria-hidden="true"></span>' if cls == "going" else ""
    track = (_node("done", "Найдена", [found]) + _node(check_cls, spin + esc(check_title), check, kind=cls)
             + _node(went_cls, esc(went_title), went))
    return (f'<li class="pm-row mx-item" data-state="{esc(state)}" data-key="{key}" data-loose="1">'
            f'<span class="pm-pic"><img alt="" loading="lazy" decoding="async" '
            f'src="/api/parser/manga/cover?k={key}&amp;w=0"></span>'
            f'<div class="pm-what"><b>{esc(row["name"])}</b><span class="pm-size">{esc(row["size"])}</span>'
            f'<span class="pm-path">{esc(row["where"])}</span></div>'
            f'<div class="mx-track">{track}</div>'
            f'<div class="pm-tools">{"".join(tools)}</div>{_link_form(row)}{files}</li>')


def _row(settings: dict, row: dict, live: bool, counted: bool, items: dict) -> str:
    """A folder as a track of three points (v5.44.160, the mockup B): «Найдена» — what it looked like a manga by;
    what the check said and why; where the folder went."""
    from core import parser_manga as PM
    if row.get("loose"):                                # (v5.44.164) a manga of the loose pictures
        return _loose_row(row, live)
    during_run = live and PM.run_handles(settings, row["key"])
    cls, words = _kind(row, live, during_run=during_run)
    works = row["works"]
    check, went = [], []                 # the lines of the second and the third point
    went_cls, went_title = "todo", "—"
    gallery = 0
    if row["state"] in ("manga", "part"):
        first = next((w for w in works if w["state"] == "manga"), works[0])
        check_cls = "done"
        check_title = "Подтверждена" + (f" — {sum(1 for w in works if w['state'] == 'manga')} из {len(works)}"
                                         if row["state"] == "part" else "")
        check.append(f'<span>{esc(first.get("by", ""))}</span>')
        others = [w for w in works if w["state"] not in ("manga", "")]
        if row["set"] and others:
            check.append('<span class="pm-sub">' + "; ".join(f"«{esc(w['name'])}» — {esc(w.get('by', ''))}"
                                                              for w in others[:3]) + "</span>")
        went_cls, went_title = "good", "В «Манге»"
        pending_photos = [w for w in works if w.get("eh_photo") and not w.get("page")]
        if pending_photos:
            went_cls, went_title, _text = _eh_photo_status(pending_photos[0])
        if row["set"]:
            folders = sum(w["parts"] for w in works)
            manga = sum(1 for w in works if w["state"] == "manga")
            went.append(f"<span>{_plural(folders, ('папка', 'папки', 'папок'))} → "
                        f"{_plural(manga, ('манга', 'манги', 'манг'))}"
                        + (f", одна сборка «{esc(row['name'])}»" if manga >= 2 else "") + "</span>")
        source = ('<p class="pm-sub">обложка и теги — со страницы галереи E-Hentai</p>'
                  if first.get("eh_photo") else _source_line(first))
        went.append(source)
        for work in works:
            if work.get("error"):
                went.append(f'<p class="pm-sub">«{esc(work["name"])}»: {esc(work["error"])}</p>')
            if work.get("eh_photo"):
                _tone, _title, text = _eh_photo_status(work)
                went.append(text)
                continue
            fill = _fill_line(work)
            if not row["set"] and fill == '<p class="pm-sub">все главы на месте</p>' and fill[18:-4] in source:
                fill = ""                               # (v5.44.166) said by the line above already — not twice
            if not row["set"] and (work.get("site") or {}).get("gallery"):
                fill = ""                               # (v5.44.169) a gallery's pages: its own line says them
            if fill:
                went.append(fill.replace('<p class="pm-sub">', f'<p class="pm-sub">«{esc(work["name"])}»: ', 1)
                            if row["set"] else fill)
        if not any(went):
            went.append("<span>копия; исходная папка не трогается</span>")
    elif row["state"] == "not":
        nots = [w for w in works if w["state"] == "not"] or works
        later = next((w for w in nots if w.get("how") == "later"), None)
        handed = all(w.get("handed") for w in nots)
        by = works[0].get("by", "") if works else ""
        pictures = row.get("pages") or 0
        check_cls = "no"
        if later is not None:                           # SauceNAO not to be asked when it was checked: asked later
            check_title = "Не подтвердилась"
            check.append("<span>ни файлы, ни имя мангу не подтвердили</span>")
            check.append(f'<span class="pm-sub">SauceNAO спрошу {esc(_when(later.get("sauce_at", 0)))}: если '
                         'подтвердит мангу, перенесу в «Мангу» (её картинки из галереи — в корзину, их можно '
                         'вернуть)</span>')
        else:
            check_title = "Не манга"
            check.append(f"<span>{esc(by)}</span>")
        if not handed:                                  # found «not a manga» by the background after the run
            went_cls, went_title = "todo", "Ждёт парсера"
            went.append(f"<span>{_plural(pictures, PM.PICTURES)}: пропарсится при следующем запуске парсера</span>")
        else:
            went_cls, went_title = "done", "В галерее"
            went.append(f"<span>{_plural(pictures, PM.PICTURES)} — на booru-сайты, как все картинки, сборкой в "
                        "галерее</span>")
            if counted:
                found, brak, set_ids = 0, 0, []
                for work in works:
                    one = PM._set_said(settings, _work_of(row, work["key"], items))
                    found, brak = found + one[0], brak + one[1]
                    if one[3]:
                        gallery = gallery or one[3]
                    if one[2]:
                        set_ids.append(one[2])
                if found or brak:
                    went.append(f'<span class="pm-sub">найдено {found} · в Браке {brak}'
                                + (f" · одна сборка «{esc(row['name'])}»" if set_ids else
                                   " · сборка соберётся после парсинга") + "</span>")
                else:
                    went.append('<span class="pm-sub">сборка соберётся после парсинга</span>')
    elif row["state"] == "wait":
        work = next((w for w in works if w["state"] == "wait"), works[0])
        check_cls, check_title = "now", words
        check.append(f'<span>{esc(work.get("by", ""))} — <b>проверю {esc(_when(work.get("later_at", 0)))}</b></span>')
        went_title = "Пока никуда"
        went.append("<span>ни в «Мангу», ни на booru-сайты: без подтверждения ничего не делается</span>")
    else:
        check_cls, check_title = "todo", words
        check.append("<span>" + ("ComicInfo.xml и ссылка в файлах, потом тайтл на сайтах манги по имени (и по "
                                 "названию пула e621), потом SauceNAO" if live else
                                 "проверю в следующем запуске парсера или в фоне") + "</span>")
        went_title = "Пока никуда"
        went.append("<span>ни в «Мангу», ни на booru-сайты</span>")
    if row.get("again"):                                # A request can wait behind the parser or another row.
        later = _again_later(row)
        why = next((w.get("again_why") for w in works if w.get("again") and w.get("again_why")), "")
        previous = check if row["state"] in ("not", "wait", "manga", "part") else []
        check = []
        check_cls, check_title = "todo", words
        waiting = ("в очереди манги" if during_run else "после завершения основного парсера" if live else "по очереди")
        if any(w.get("again_link") for w in works):     # (v5.44.169) «Указать ссылку»
            check.append((f'<span>открою ссылку {esc(_when(later))}' + (f": {esc(why)}" if why else "") + "</span>")
                         if later else f"<span>ссылка поставлена в очередь: {waiting} открою страницу по вашей "
                                       "ссылке и сверю её страницы с папкой</span>")
        elif row.get("again_auto") and not later:       # (v5.44.169) a folder of the rules of before, in the queue
            check.append("<span>обновлены правила распознавания — перепроверю сама: по названию, потом "
                         "SauceNAO</span>")
        else:
            check.append((f'<span>перепроверю {esc(_when(later))}' + (f": {esc(why)}" if why else "") + "</span>")
                         if later else f"<span>перепроверка запрошена: {waiting} заново сверю с сайтами манги "
                                       "файлы папки, её название, SauceNAO</span>")
        if previous:
            check.append('<div class="pm-sub">Предыдущая проверка: ' + "".join(previous) + "</div>")
    else:
        check.append(_again_said(row))
    spin = '<span class="pm-spin" aria-hidden="true"></span>' if cls == "going" else ""
    stack = " stack" if row["set"] else ""
    extra = _parts(row, row.get("notes") or []) if row["set"] or any(w["parts"] > 1 for w in works) else ""
    track = (_node("done", "Найдена", [f"<span>{esc(_found_by(row))}</span>"])
             + _node(check_cls, spin + esc(check_title), check, kind=cls)
             + _node(went_cls, esc(went_title), went))
    return (f'<li class="pm-row mx-item" data-state="{esc(row["state"])}" data-key="{esc(row["key"])}">'
            f'<span class="pm-pic{stack}"><img alt="" loading="lazy" decoding="async" '
            f'src="/api/parser/manga/cover?k={esc(row["key"])}&amp;w=0"></span>'
            f'<div class="pm-what"><b>{esc(row["name"])}</b><span class="pm-size">{esc(row["size"])}</span>'
            f'<span class="pm-path">{esc(row["where"])}</span></div>'
            f'<div class="mx-track">{track}</div>'
            f'<div class="pm-tools">{_tools(row, gallery)}</div>{_link_form(row)}{extra}</li>')


def _work_of(row: dict, key: str, items: dict):
    from core import manga_detect as MD
    item = items.get(row["key"]) or {}
    cand = MD.Candidate.of(item.get("cand") or {})
    return next((w for w in cand.works if w.key == key), cand.works[0] if cand.works else MD.Work(key="", name="",
                                                                                                 path=""))


def _loose_waiting(settings: dict) -> int:
    """(v5.44.164) Pictures of Брак still to be asked of SauceNAO again (``parser_loose.waiting``)."""
    from core import parser_loose
    try:
        return int(parser_loose.waiting(settings))
    except (OSError, ValueError) as exc:               # its file not read: the block says nothing of them this time
        logging.getLogger("local_booru.manga").info("the loose pictures' file not read: %s", exc)
        return 0


def _live() -> bool:
    """The parser runs (its manga are checked beside the files) — the page asks again often."""
    from core.parser import jobs
    return jobs.is_running()


FILTERS = (("all", "все папки", ""), ("manga", "в «Манге»", "g"), ("not", "не манга", ""), ("wait", "ждут сайты", "w"),
           ("check", "не проверены", ""))
ICON_FOLDER = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" '
               'stroke-linejoin="round" aria-hidden="true"><path d="M3.5 7.5a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v8.5'
               'a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z"/></svg>')
ICON_FIND = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/></svg>')
ICON_YES = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>')
ICON_NO = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
           'aria-hidden="true"><path d="M6 12h12"/></svg>')


def total(settings: dict) -> int:
    """How many folders of the parser look like a manga (the number on the tab «Манга из папки»)."""
    from core import parser_manga as PM
    return int(PM.summary(settings).get("total") or 0)


def body(settings: dict, *, wanted: str = "all", every: bool = False) -> str:
    """The block's inside (the page and ``/api/parser/manga`` give the same)."""
    from core import manga_detect as MD, manhwa_search as MS, manhwa_sources as S, parser_manga as PM
    data = PM.summary(settings)
    live = _live()
    asked = esc(", ".join(s.label for s in MS.sources()))            # (v5.44.169) the sites a name is searched on
    by_link = esc(", ".join(s.label for s in S.SOURCES if s.readable and not s.by_name))
    late_wait, late_tries = PM.LATE_WAIT // 60, PM.LATE_TRIES
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
    per_day = int(data.get("per_day") or 0)
    # how it works: three steps (the mockup B)
    flow = f"""<div class="mx-flow" aria-label="Как это работает">
    <div class="mx-step">{ICON_FOLDER}<b>1. Папка или картинка похожа на мангу</b>
      <span>папка — страницы по порядку, главы, ComicInfo.xml или ссылка на сайт в файлах; отдельная картинка —
      SauceNAO узнал в ней страницу манги</span></div>
    <span class="mx-arrow" aria-hidden="true">→</span>
    <div class="mx-step">{ICON_FIND}<b>2. Сверяю с сайтами манги</b>
      <span>по файлам и названию, потом по картинке через SauceNAO — не больше {per_day} в день, сегодня
      осталось {left}</span></div>
    <span class="mx-arrow" aria-hidden="true">→</span>
    <div class="mx-ends">
      <div class="mx-step yes">{ICON_YES}<b>Подтвердилась — копия в «Мангу»</b><span>исходная папка не трогается</span></div>
      <div class="mx-step no">{ICON_NO}<b>Нет — в галерею, как все картинки</b></div>
    </div>
  </div>"""
    switches = f"""<div class="pm-switches mx-set">
    <label class="pm-sw"><input type="checkbox" class="switch" data-pm-switch="parser_skip_manga"{" checked" if skip else ""}>
      <b>Проверять папки и класть мангу в «Мангу»</b></label>
    <label class="pm-sw"><input type="checkbox" class="switch" data-pm-switch="parser_manga_fill"{" checked" if fill else ""}>
      <b>Докачивать главы, которых нет в папке</b></label>
    <details class="mx-why"><summary>Как программа решает, что это манга</summary>
      <p>Сначала ComicInfo.xml и ссылка в файлах, потом тайтл на сайтах манги и галерея на nhentai — по имени папки
        и названию пула e621 (если её страницы есть на e621); страницы сверяются с тайтлом. SauceNAO — только если
        это ничего не дало: 1–2 страницы на мангу, вместе с «Из архива» — не больше {per_day} поисков в день, сегодня
        осталось {left}. Его спрашиваю и про мангу (MangaDex), и про хентай-мангу (страницы галерей nhentai и
        e-hentai) — одним поиском.</p>
      <p><b>По названию папки.</b> Название ищется без пометок в скобках («[Группа] Название (2019) [RUS]» —
        ищется «Название») и без того, чем сайты мангу не называют: «_» вместо пробела, «18+», «+18», «R-18»,
        «NSFW» («Название_18+» ищется как «Название»). У найденных тайтлов программа берёт первые страницы главы и
        сверяет с первыми страницами папки (обложка или страница переводчиков в начале не мешают). Тайтл называется
        так же — достаточно одной совпавшей страницы; называется иначе — нужны две, с совпадением от 90 %. Одно
        название, без страниц, мангу не подтверждает никогда. Если не подтвердилось, в строке написано почему: по
        названию ничего не нашлось — или нашёлся тайтл, но страницы у него другие.</p>
      <p><b>Где ищется название:</b> {asked} и nhentai (на нём — только нерусские названия: русского там нет).
        {by_link} пускают только браузер (проверка «не робот»), поэтому сама программа, в фоне, название там не ищет:
        их она спрашивает, когда вы сами жмёте «Перепроверить» у папки (через браузер своего окна, как ваш поиск в
        «Ленте» этих сайтов), — или дайте их тайтл ссылкой. <b>Если сайт не ответил</b>, папка не
        получает «не манга» сразу: она ждёт и переспрашивает молчавшие сайты — через {late_wait} минут, до
        {late_tries} раз; не ответили и тогда — в строке написано, какие («Перепроверить» спросит их снова).</p>
      <p><b>nhentai.</b> Галерея подтверждает папку так же — страницами: первые страницы папки сверяются с первыми
        страницами галереи (у одной работы на нескольких языках берётся та, чьи страницы ближе). Копия в «Манге»
        получает название, описание и теги галереи; в строке написано, сколько её страниц есть в папке. Если не
        все и страницы копии пронумерованы как у галереи — недостающие докачиваются сами (при включённом
        «Докачивать главы»; иначе в строке есть «Докачать»); пронумерованы иначе — не докачиваются: те же страницы
        легли бы в папку дважды. Если SauceNAO узнал страницу, а галереи с такими страницами на nhentai нет, в строке
        будет её название — дальше решаете вы: «Указать ссылку» или «Это манга».</p>
      <p><b>«Указать ссылку»</b> — когда программа мангу не узнала или узнала не ту: вставьте ссылку на её страницу
        (тайтл на сайте манги или галерея nhentai). Программа откроет страницу, скопирует папку в «Мангу», возьмёт
        описание и теги и докачает главы, которых нет в папке. Страницы она всё равно сверит и напишет, совпали ли, —
        но решает ваша ссылка; перепроверка потом её не заменит.</p>
      <p><b>«Перепроверить»</b> у папки — программа сверит её заново: файлы, название (и на сайтах, которые
        пускают только браузер), SauceNAO. Подтвердится —
        копия уйдёт в «Мангу», а картинки, которые парсер уже положил в галерею, — в корзину (их можно вернуть).
        Мангу, которая уже в «Манге», перепроверка не уберёт: не подтвердилась в этот раз — остаётся как была
        (убрать её может только ваше «Это не манга»); нашёлся другой тайтл — описание, теги и главы возьмутся с
        него. «Перепроверить все «не манга»» — сразу все такие папки, по файлам и названию.</p>
      <p><b>Папка с несколькими мангами</b> — каждая манга сама по себе. Одной сборкой в «Манге» они становятся,
        только если папка названа как одна из них («silver soul» с «Сильвер Соул» внутри); папка-раздел — жанр,
        автор, «прочитано» — сборкой не становится.</p>
      <p>В «Мангу» попадает только подтверждённое сайтом манги: <b>ComicInfo.xml, ссылка в файлах или тайтл на сайте
        манги (галерея nhentai), чьи страницы совпали со страницами папки</b> (по имени папки, названию пула e621 или
        ответу SauceNAO) — и то, что назвали мангой вы сами.
        Пул e621 сам по себе мангу не подтверждает. Не подтвердилось — не манга: страницы идут на booru-сайты, как
        обычно, и сборкой в галерею. Пока сайты манги не отвечают — папка ждёт; кончился лимит SauceNAO — папка
        парсится как обычно, а SauceNAO спросится позже: подтвердит мангу — перенесу в «Мангу».</p>
      <p>«Докачивать главы»: главы, которых нет в папке, — с сайта манги, который её подтвердил (видно в
        «<a href="/get/downloads">Загрузках</a>»); платные — никогда. Файлы исходной папки не трогаются — в «Манге»
        копия. Первый выключатель выключен — парсер берёт все папки как обычно.</p>
      <p><b>Страница манги без папки</b> — картинка, которая лежит среди обычных. Парсер и так спрашивает SauceNAO о
        каждой картинке, которую не нашли booru-сайты, и SauceNAO в том же ответе говорит, если это страница манги
        (какой и из какой главы), — лишних поисков на это не уходит. Программа находит тайтл на сайтах манги и
        сверяет картинку со страницами этой главы. Совпала — тайтл скачивается в «Мангу» (если включено «Докачивать
        главы»; начнётся, когда вы отойдёте от компьютера), а картинка уходит из «Брака» в корзину — её можно вернуть.
        Не совпала — остаётся в «Браке», как была. <b>Страница галереи nhentai</b> — так же: SauceNAO называет
        галерею, программа находит её на nhentai и ищет картинку среди её страниц; нашла — галерея скачивается в
        «Мангу» целиком, картинка уходит из «Брака». Картинки, которые уже лежат в «Браке» с таким ответом SauceNAO,
        программа переспросит сама — понемногу, не больше {max(1, per_day // 2)} в день.</p>
    </details>
  </div>"""
    if not rows:
        summary = ("Папок, похожих на мангу, пока нет: парсер смотрит на них при запуске, фон — раз в несколько минут."
                   if skip else "Выключено: парсер берёт все папки как обычно.")
        counters = ""
    else:
        # the counts are the filters; under them — what the counts alone do not say
        def chip(key: str, label: str, tone: str) -> str:
            cls = " ".join(c for c in ("on" if key == wanted else "", tone) if c)
            return ('<button type="button"' + (f' class="{cls}"' if cls else "")
                    + f' data-pm-filter="{key}" aria-pressed="{"true" if key == wanted else "false"}">'
                    + f'<b>{len(groups[key])}</b> {"проверяю" if key == "check" and live else label}</button>')
        loose = any(r.get("loose") for r in rows)       # (v5.44.164) not folders alone: the first chip says «все»
        counters = ('<nav class="pm-filter mx-filter" aria-label="Какие папки показать">'
                    + "".join(chip(one[0], "все" if loose and one[0] == "all" else one[1], one[2])
                              for one in FILTERS if one[0] == "all" or groups[one[0]]) + "</nav>")
        said = []
        later = int(data.get("later") or 0)
        if later:
            said.append(f"У {_plural(later, ('папки', 'папок', 'папок'))} «не манга» SauceNAO спрошу позже"
                        + (" — лимит на сегодня кончился" if left <= 0 else "")
                        + ": подтвердит мангу — перенесу в «Мангу».")
        if data["wait"]:
            why = _wait_words(data.get("waits") or [])
            if why:
                said.append(f"Ждут: {why}.")
        summary = " ".join(said)
    # (v5.44.166) «Перепроверить все „не манга"»: the rows the program (not the owner, not a gate) called so
    again_can = sum(1 for r in groups["not"] if r.get("hand") != "not" and not r.get("again") and any(
        w["state"] == "not" and w.get("how") not in ("forbidden", "later", "hand") for w in r["works"]))
    manual = [r for r in rows if r.get("again") and not r.get("again_auto")]
    again_pending = len(manual)
    again_queued = sum(1 for r in rows if r.get("again_auto"))      # (v5.44.169) …by the program itself
    again = ""
    if again_pending:
        running = sum(1 for r in manual if live and not r.get("loose") and PM.run_handles(settings, r["key"]))
        waiting = "В очереди манги" if running == again_pending else (
            "После завершения парсера" if live and not running else "Запрошена перепроверка")
        after = (f" ({again_pending - running} после завершения парсера)" if 0 < running < again_pending else "")
        again = (f'<p class="pm-again">{waiting}: '
                 f'{_plural(again_pending, ("папка", "папки", "папок"))}{after} — сайты манги спрашиваю не чаще '
                 f'{PM.SEARCHES_PER_HOUR} раз в час, остальное доделаю сама, страницу можно закрыть</p>')
    elif again_queued and skip:
        again = (f'<p class="pm-again">После обновления программа спрашивает и nhentai — папки «не манга» '
                 f'перепроверяю сама, в фоне: осталось {again_queued}. Сначала по названию, потом SauceNAO '
                 f'(сайты манги — не чаще {PM.SEARCHES_PER_HOUR} поисков в час; новые папки идут раньше '
                 'очереди). Нужна папка сейчас — «Перепроверить» или «Указать ссылку» в её строке.</p>')
    elif again_can and skip:
        one = again_can % 10 == 1 and again_can % 100 != 11
        again = (f'<p class="pm-again"><button type="button" class="link" data-action="pm-again-all" data-ask="'
                 f'Перепроверить {_plural(again_can, ("папку", "папки", "папок"))}? Нажмите ещё раз">'
                 f'Перепроверить все «не манга» ({again_can})</button> — заново по файлам и названию '
                 f'{"папки" if one else "папок"}, без SauceNAO (его поисков в день мало; нужен он — '
                 f'«Перепроверить» в строке папки)</p>')
    old = _loose_waiting(settings)                      # (v5.44.164) Брак's old pictures SauceNAO named a manga for
    if old and skip:
        one = old % 10 == 1 and old % 100 != 11
        summary = (summary + " " if summary else "") + (
            f"В «Браке» {_plural(old, ('картинка', 'картинки', 'картинок'))}, у "
            f"{'которой' if one else 'которых'} SauceNAO раньше назвал мангу: переспрошу его "
            f"{'о ней' if one else 'о них понемногу'} — не больше {max(1, per_day // 2)} в день.")
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
    return f"""<h2 class="pm-h" id="pm-h">Манга в исходной папке</h2>
  {flow}
  {switches}
  {counters}
  {f'<p class="pm-sum">{summary}</p>' if summary else ""}
  {again}
  {f'<ol class="pm-rows mx-list">{listed}</ol>' if shown else ""}
  {more}
  <span class="action-status" role="status"></span>"""


def block(settings: dict) -> str:
    """The tab «Манга из папки» of the page «Парсер»."""
    return (f'<section class="pm" id="parser-manga" aria-labelledby="pm-h" data-pm-url="/api/parser/manga" '
            f'data-live="{"1" if _live() else ""}"><div class="pm-body">{body(settings)}</div></section>')


# ── the API ────────────────────────────────────────────────────────────────────────────────────────────────────────
def state(req: Request) -> Response:
    from core import parser_manga as PM
    wanted = req.arg("state") or "all"
    every = req.arg("all") == "1"
    data = PM.summary(req.settings)
    rows = [{k: v for k, v in r.items() if k in ("key", "name", "where", "set", "state", "size", "pages", "loose",
                                                 "again")} |
            {"works": [{k: v for k, v in w.items() if k in ("key", "name", "state", "how", "by", "size", "page",
                                                              "later_at", "fill", "sauce_at", "handed",
                                                              "again_link")}
                       for w in r["works"]]}
            for r in data["rows"]]
    return Response.json({"ok": True, "html": body(req.settings, wanted=wanted, every=every), "live": _live(),
                          "fast": _fast(data["rows"]),
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


def look_again(req: Request) -> Response:
    """(v5.44.166) «Перепроверить» of a row ({key}) and «Перепроверить все „не манга"» ({all: true})."""
    from core import parser_manga_again as PA
    body_ = req.json() or {}
    got = PA.recheck_all(req.settings) if body_.get("all") is True else PA.recheck(req.settings, _key(req))
    return Response.json(got, 200 if got.get("ok") else 404)


def link(req: Request) -> Response:
    """(v5.44.169) «Указать ссылку» of a row: {key, url[, work]} — the manga of it is the title at the link."""
    from core import parser_manga_again as PA
    body_ = req.json() or {}
    got = PA.say_link(req.settings, _key(req), str(body_.get("url") or ""), str(body_.get("work") or "")[:64])
    if got.get("ok"):
        return Response.json(got)
    return Response.json(got, 404 if "в списке нет" in str(got.get("error")) else 400)


def download_now(req: Request) -> Response:
    """(v5.44.164) «Скачать сейчас» of a manga of the loose pictures whose download waits for the person to leave (or
    did not come)."""
    from core import parser_manga as PM
    got = PM.download_now(req.settings, _key(req))
    return Response.json(got, 200 if got.get("ok") else 409 if "уже" in str(got.get("error")) else 404)


def switch(req: Request) -> Response:
    """The block's switches: «Проверять папки и класть мангу в «Мангу»» and «Докачивать главы, которых нет в папке»
    (the settings)."""
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
