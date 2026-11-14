"""(v5.44.166) «Манга через парсер», looked at again: «Перепроверить» of a row, «Перепроверить все „не манга"», and
what a change of the rules does to what the rules of before made (a folder of a kind is a сборка no more).

The owner (12.11.2026): «нужна возможность перепроверить мангу которая через парсер там сейчас это не манга и открыть в
манге а нужно что бы была возможность перепроверить».  Until now a folder was looked at once: «не манга» was for good
(but for SauceNAO put off), a manga stayed linked to the title that confirmed it, and only the owner's own word («Это
манга», «Это не манга») changed anything.

* :func:`recheck` — a row (a folder's candidate; a manga of the loose pictures — ``parser_loose.recheck``): each
  manga of it is marked (:data:`AGAIN` in its record: when, what it was, by which ways) and looked at anew by rounds
  started at once (:func:`start`: a task of the pages — it ends with the program) — by all the ways: its files, its
  names on the sites of manga, SauceNAO.  His «Это не манга» of before is taken back (he asks the program again).
* :func:`recheck_all` — every manga the program (not the owner, not a gate) called «не манга»: its files and its
  names alone — SauceNAO has said its word of them, and its searches of the day are few.
* What a second look may do (:func:`settle`): **confirm** what was not — as any confirmation (a copy in «Манга», its
  link, the chapters it misses; the cards the parser made of its pages go to the trash «перенесено в мангу», as when
  SauceNAO confirms later); **name another title** for a manga confirmed before — its copy's link moves to that title;
  and **never take a confirmation back**: a manga in «Манга» the sites do not confirm this time (the title gone from
  the site, another scan there now) stays what it was, and the row says so — «Это не манга» is the owner's word.  One
  thing does: a gate of the program that closes the folder now.
* No site answering, the hour's searches spent — the mark stays and the look goes on later (the duty «comic_manga»);
  the parser running — after it.
* :func:`unset_copies` — the copies in the manga folder made as one сборка of a folder that is one no more
  (``manga_detect.named_after``, 5.44.166: a folder of a kind — «bdsm»): ``lb_set`` of their ``info.json`` taken out,
  once.  Each is a manga by itself in «Вся манга» then.

(v5.44.169) **«Указать ссылку»** — the owner (12.11.2026): «почему нет возможности просто взять и вставить ссылку на
мангу если она не подтвердилась?».  :func:`say_link` — a row's manga is marked with his link (a title of a site of
manga of the program, a gallery of nhentai) and looked at at once (:func:`_by_hand`): the link is opened — through the
program's gates, as any title is (``Source.title`` → ``manhwa_sources.checked``; ``manga_download.gallery_of``) — and
**his word confirms**: the folder is that title's, copied into «Манга», described and tagged by its page, the chapters
it misses go into «Загрузки» (``settle`` — as any confirmation: a copy made before moves to this title).  The pages
are compared all the same: they say which of the site's chapters the folder is, and the row says what they said (a
wrong link shows: «страницы не совпали»).  A link the program does not know, a title a gate closes, one that is not
there — refused in words, nothing changes; a site that does not answer — asked again (:data:`LINK_TRIES` times).  A
second look later never moves a title he linked himself to another one.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from core import manga_detect as MD

_LOG = logging.getLogger("local_booru.manga")

AGAIN = "again"                 # a record's mark: {"at", "state" (what it was), "url", "title", "steps", "later_at"}
SAID = "again_said"             # …and what the second look said: {"at", "was", "state", "by", "kept", "moved"}
ALL_STEPS = ("files", "names", "pictures")
NAME_STEPS = ("files", "names")
TASK = "parser_manga_again"
SET_RULE = 2                    # (5.44.166) a folder of several manga — their сборка only when named after one of them
BUSY_WAIT = 90                  # seconds the rounds wait for a round of the duty going, then leave it to the duty
HAND_STEPS = ("hand",)          # (v5.44.169) a mark's ways when the owner gave a link: none of the program's own
LINK_LATER = 5 * 60             # …its site did not answer: asked again so much later
LINK_TRIES = 3                  # …so many times, then the link is given back to him («сайт не отвечает»)
LINK_MAX = 2000


def _mark(rec: dict, steps: tuple, now: int) -> dict:
    return {"at": now, "token": uuid.uuid4().hex, "state": str(rec.get("state") or ""), "url": str(rec.get("url") or ""),
            "title": str(rec.get("title") or ""), "steps": list(steps), "later_at": 0}


def _current(cand, work, rec: dict, mark: dict) -> bool:
    """A reply belongs only to the exact queued choice it started with, including same-second requests."""
    from core import parser_manga as PM
    item = PM.STORE.items().get(cand.key) or {}
    current = PM.state_of(item, work)
    fields = ("state", "hand", "url", "copy", "copied", "eh_photo")
    return bool(mark) and current.get(AGAIN) == mark and all(current.get(k) == rec.get(k) for k in fields)


def _put_current(cand, work, rec: dict, mark: dict, values: dict) -> bool:
    """Compare and publish a short result atomically; user choices never wait for network operations."""
    from core import parser_manga as PM
    with PM.STORE._lock:
        if not _current(cand, work, rec, mark):
            return False
        PM.STORE.put_work(cand.key, work.key, values)
        return True


def marked(settings: dict) -> int:
    """How many manga of the parser's folder are still to be looked at again (now or later)."""
    from core import parser_manga as PM
    root = str((settings or {}).get("root") or "").strip()
    count = 0
    for item in (PM.STORE.items(root) if root else {}).values():
        for rec in (item.get("works") or {}).values():
            if isinstance(rec, dict) and rec.get(AGAIN):
                count += 1
    return count


def _due_now(settings: dict) -> int:
    """How many manga marked :data:`AGAIN` may be looked at now (not put off to later, not the owner's «не манга»)."""
    from core import parser_manga as PM
    root = str((settings or {}).get("root") or "").strip()
    now = time.time()
    count = 0
    for item in (PM.STORE.items(root) if root else {}).values():
        if item.get("hand") == PM.NOT:
            continue
        for rec in (item.get("works") or {}).values():
            mark = rec.get(AGAIN) if isinstance(rec, dict) else None
            if mark and float(mark.get("later_at") or 0) <= now and rec.get("hand") != PM.NOT:
                count += 1
    return count


# ── the owner's «Перепроверить» ─────────────────────────────────────────────────────────────────────────────────────
def recheck(settings: dict, key: str) -> dict:
    """«Перепроверить» of a row: {"ok", "count", "words"} — or {"ok": False, "error"}."""
    from core import parser_manga as PM
    if str(key).startswith(PM.LOOSE):
        count = _loose_mark(str(key))
        if count is None:
            return {"ok": False, "error": "Такой строки в списке нет — откройте страницу заново"}
        return {"ok": True, "count": count, "words": _started_words(settings)}
    item = PM.STORE.items().get(str(key))
    cand = PM._cand(item or {})
    if cand is None:
        return {"ok": False, "error": "Такой папки в списке нет — откройте страницу заново"}
    refused = MD.not_manga()
    taken_back = False
    for path in [cand.path, *cand.roots()]:             # his «Это не манга» of before: he asks the program again
        if MD._norm(path) in refused:
            MD.set_not_manga(path, False)
            taken_back = True
    if taken_back:
        MD.STORE.accept()
    now = int(time.time())

    def put(items: dict) -> int:
        kept = items.get(str(key))
        if kept is None:
            return 0
        if kept.get("hand") == PM.NOT:
            kept.pop("hand", None)
            if isinstance(kept.get("cand"), dict):
                kept["cand"]["refused"] = False
        count = 0
        for work in cand.works:
            rec = kept.setdefault("works", {}).setdefault(work.key, {"name": work.name, "state": ""})
            if rec.get("hand") == PM.NOT:
                rec.pop("hand", None)
            was = PM.MANGA if kept.get("hand") == PM.MANGA else rec.get("state")
            # (v5.44.170) ``wide``: his word about this one folder — the sites of the window's browser are asked too
            rec[AGAIN] = {**_mark(rec, ALL_STEPS, now), "state": str(was or ""), "wide": True}
            rec.pop(SAID, None)
            count += 1
        return count
    count = int(PM.STORE.change(put) or 0)
    if not count:
        return {"ok": False, "error": "Такой папки в списке нет — откройте страницу заново"}
    return {"ok": True, "count": count, "words": _started_words(settings)}


def recheck_all(settings: dict) -> dict:
    """«Перепроверить все „не манга"»: every manga of the parser's folder the program called «не манга» (not the
    owner's own «Это не манга», not one a gate of the program closed, not one whose SauceNAO is still to be asked) —
    by its files and its names: {"ok", "count", "words"}."""
    from core import parser_manga as PM
    root = str((settings or {}).get("root") or "").strip()
    now = int(time.time())

    def put(items: dict) -> int:
        count = 0
        for item in items.values():
            if not isinstance(item, dict) or MD._norm(item.get("root") or "") != MD._norm(root) or item.get("hand"):
                continue
            for rec in (item.get("works") or {}).values():
                if not isinstance(rec, dict) or rec.get("hand") or rec.get(AGAIN):
                    continue
                if rec.get("state") != PM.NOT or rec.get("how") in ("forbidden", "later", "hand"):
                    continue
                rec[AGAIN] = _mark(rec, NAME_STEPS, now)
                rec.pop(SAID, None)
                count += 1
        return count
    count = int(PM.STORE.change(put) or 0) if root else 0
    try:                                                # …and the manga of the loose pictures called so
        count += int(_loose_mark("") or 0)
    except (OSError, ValueError) as exc:
        _LOG.warning("parser manga: the loose pictures not marked to look at again: %s", exc)
    if not count:
        return {"ok": True, "count": 0, "words": "Перепроверять нечего: папок «не манга», которые назвала так "
                                                 "программа, нет"}
    return {"ok": True, "count": count, "words": _started_words(settings)}


def mark_old(root: str) -> int:
    """(v5.44.169; ``parser_manga.background_round``) The folders the program called «не манга» by the rules of
    before (``parser_manga.RULE`` — nhentai was no site that confirms a folder, SauceNAO was not asked about its
    indexes of hentai) are looked at again by themselves, once: marked as «Перепроверить все „не манга"» marks them
    (their files and names — nhentai among the sites now), and one the names do not confirm then is «не манга» for
    now whose SauceNAO is asked later (the mark's ``sauce``: within the day's searches, as ever).  One whose SauceNAO
    was still to be asked is marked too: its names were searched by the rules of before (with «_» and «18+» in
    them, a silent site taken for «no») — searched anew, they cost none of SauceNAO's searches of the day.  Not the
    owner's own «Это не манга», not one a gate closed.  How many were marked.  The background looks at them after
    everything that is new and leaves them none of the hour's last searches (``parser_manga.AUTO_KEEPS``)."""
    from core import parser_manga as PM
    mine = MD._norm(root)

    def old(rec) -> bool:
        return isinstance(rec, dict) and int(rec.get("rule") or 0) < PM.RULE
    if not any(old(rec) for item in PM.STORE.items(root).values() for rec in (item.get("works") or {}).values()):
        return 0
    now = int(time.time())

    def put(items: dict) -> int:
        count = 0
        for item in items.values():
            if not isinstance(item, dict) or MD._norm(item.get("root") or "") != mine:
                continue
            for rec in (item.get("works") or {}).values():
                if not old(rec):
                    continue
                rec["rule"] = PM.RULE
                if item.get("hand") or rec.get("hand") or rec.get(AGAIN):
                    continue
                if rec.get("state") != PM.NOT or rec.get("how") in ("forbidden", "hand"):
                    continue                            # (one whose SauceNAO is still to be asked — marked too: its
                rec[AGAIN] = {**_mark(rec, NAME_STEPS, now), "auto": True, "sauce": True}      # names first)
                count += 1
        return count
    count = int(PM.STORE.change(put) or 0)
    if count:
        _LOG.info("parser manga: %d folders called «не манга» by the rules of before are looked at again", count)
    return count


def link_words() -> str:
    """Whose links «Указать ссылку» takes, in words."""
    from core import manhwa_sources as S
    labels = [s.label for s in S.SOURCES if s.readable]
    return ", ".join(labels) + " или галерея nhentai (nhentai.net/g/номер)"


def link_known(url: str) -> bool:
    """A link «Указать ссылку» takes: a title of a site of manga of the program, one of MangaDex's chapters (its
    title is asked), a gallery of nhentai."""
    from core import manga_clues as C, parser_manga_nhentai as PN
    return bool(PN.number_of(url)) or C.known_link(url)


def say_link(settings: dict, key: str, url: str, work: str = "") -> dict:
    """(v5.44.169) «Указать ссылку» of a row: the manga ``work`` of it (its key; a row of one manga — that one) is
    the title at ``url`` — marked and looked at at once: {"ok", "words"}; {"ok": False, "error"} — no such row, a link
    the program does not know (the words name the sites it knows), a row of several manga and none named."""
    from core import parser_manga as PM
    url = " ".join(str(url or "").split())[:LINK_MAX]
    if str(key).startswith(PM.LOOSE):
        from core import parser_loose_link
        return parser_loose_link.say_link(settings, str(key), url)
    if not url:
        return {"ok": False, "error": "Вставьте ссылку на страницу манги"}
    if not link_known(url):
        return {"ok": False, "error": "Эту ссылку программа не знает: нужна страница тайтла на " + link_words()}
    item = PM.STORE.items().get(str(key))
    cand = PM._cand(item or {})
    if cand is None:
        return {"ok": False, "error": "Такой папки в списке нет — откройте страницу заново"}
    chosen = next((w for w in cand.works if w.key == str(work)), None) if work else (
        cand.works[0] if len(cand.works) == 1 else None)
    if chosen is None:
        return {"ok": False, "error": "В этой папке несколько манг — раскройте список и укажите ссылку у нужной"}
    refused = MD.not_manga()
    taken_back = False
    for path in [cand.path, *cand.roots()]:             # his «Это не манга» of before: he names its title now
        if MD._norm(path) in refused:
            MD.set_not_manga(path, False)
            taken_back = True
    if taken_back:
        MD.STORE.accept()
    now = int(time.time())

    def put(items: dict) -> bool:
        kept = items.get(str(key))
        if kept is None:
            return False
        if kept.get("hand") == PM.NOT:
            kept.pop("hand", None)
            if isinstance(kept.get("cand"), dict):
                kept["cand"]["refused"] = False
        rec = kept.setdefault("works", {}).setdefault(chosen.key, {"name": chosen.name, "state": ""})
        if rec.get("hand") == PM.NOT:
            rec.pop("hand", None)
        was = PM.MANGA if kept.get("hand") == PM.MANGA and rec.get("state") != PM.MANGA else rec.get("state")
        rec[AGAIN] = {**_mark(rec, HAND_STEPS, now), "state": str(was or ""), "link": url, "tries": 0}
        rec.pop(SAID, None)
        return True
    if not PM.STORE.change(put):
        return {"ok": False, "error": "Такой папки в списке нет — откройте страницу заново"}
    return {"ok": True, "words": _started_words(settings).replace("Перепроверяю", "Открываю ссылку")
            .replace("перепроверю", "открою ссылку")}


def _started_words(settings: dict) -> str:
    from core.parser import jobs as parser_jobs
    if parser_jobs.is_running():
        return "Идёт парсер — перепроверю, как он закончит"
    start(settings)
    return "Перепроверяю — результат появится в строке"


def start(settings: dict) -> bool:
    """The rounds that look again — at once (the duty «comic_manga» comes to the marks in minutes): a task of the
    pages (:data:`TASK`: one at a time; it stops with the program).  False — one goes already."""
    from core import page_jobs, parser_manga as PM
    settings = dict(settings or {})

    def run(say, stop_check):
        from core.parser import jobs as parser_jobs
        waited, rounds = 0.0, 0
        while not stop_check() and rounds < 2000:
            before = (_due_now(settings), _loose_due(settings))
            if not any(before):
                break
            if parser_jobs.is_running():
                say("Идёт парсер — перепроверю, как он закончит")
                break
            got = PM.background_round(settings, stop_check=stop_check, seconds=PM.ROUND_SECONDS, say=say)
            rounds += 1
            if got.get("busy"):                         # the duty's own round goes: after it
                if waited >= BUSY_WAIT:
                    break
                time.sleep(1.0)
                waited += 1.0
                continue
            waited = 0.0
            try:
                from core import parser_loose
                loose = parser_loose.background_round(settings, stop_check=stop_check, say=say,
                                                      seconds=float(parser_loose.ROUND_SECONDS))
            except Exception as exc:  # noqa: BLE001 - the loose pictures' trouble does not stop the folders' looks
                _LOG.warning("parser manga again: the loose pictures' round failed: %s: %s", type(exc).__name__, exc)
                loose = {}
            if got.get("no_room"):
                say(str(got["no_room"]))
                break
            if got.get("wait") or loose.get("wait"):
                say(f"Сайты манги — не больше {PM.SEARCHES_PER_HOUR} поисков в час: остальное перепроверю позже, сама")
                break
            if (_due_now(settings), _loose_due(settings)) == before and not (got.get("more") or loose.get("more")):
                break                                   # nothing moved (the switch is off, a row gone): not again
        left = marked(settings)
        say("Готово" + (f": ещё {left} — позже (сайты манги, парсер)" if left else ""))
        return {"left": left}
    try:
        page_jobs.start(TASK, "Перепроверка манги из папки парсера", run)
    except page_jobs.TaskBusy:
        return False
    return True


# ── the manga of the loose pictures (``core/parser_loose.py``) ──────────────────────────────────────────────────────
def _loose_mark(row_key: str) -> "int | None":
    """A manga of the loose pictures called «не манга» looked at again: its name searched on the sites and its
    pictures compared anew (what SauceNAO said of them is kept — it is not asked again).  ``row_key`` — its row's
    («loose:…»; the owner's «Это не манга» of it taken back); "" — every one the program called so.  How many were
    marked; None — no such row.  One confirmed is not looked at again (its title's pages are its own already)."""
    from core import parser_loose as L
    data = L.STORE.read()
    wanted = L._key_of(data, row_key) if row_key else ""
    if row_key and not wanted:
        return None
    now = int(time.time())

    def put(found: dict) -> int:
        count = 0
        for key, group in found["groups"].items():
            if wanted and key != wanted:
                continue
            if not wanted and (group.get("hand") or group.get("state") != L.NOT):
                continue
            if group.get("state") == L.MANGA:
                continue
            mine = [rec for rec in found["files"].values()
                    if rec.get("group") == key and not rec.get("trashed") and rec.get("state") in ("", L.NOT)]
            if not mine:
                continue                                # (no picture of it left to compare)
            group.pop("hand", None)
            group.update(state="", how="", by="", later_at=0, searched_at=0, tries=0, again=now)
            for rec in mine:
                rec.update(state="", why="")
            count += 1
        return count
    return int(L.STORE.change(put) or 0)


def _loose_due(settings: dict) -> int:
    """How many manga of the loose pictures marked to be looked at again may be looked at now."""
    from core import parser_loose as L
    try:
        data = L.STORE.read()
    except (OSError, ValueError):
        return 0
    now = time.time()
    return sum(1 for group in data["groups"].values()
               if group.get("again") and (group.get("state") or "") in ("", L.WAIT) and
               float(group.get("later_at") or 0) <= now and not group.get("hand"))


# ── one manga looked at again (``parser_manga.background_round``) ───────────────────────────────────────────────────
def look_again(settings: dict, asker, cand: "MD.Candidate", work: "MD.Work", *, say, stop_check,
               deadline: float) -> "dict | None":
    """A manga marked :data:`AGAIN` checked anew by the ways of its mark and what came of it done (:func:`settle`):
    the record; {"state": "", "wait": seconds} — the hour's searches of the sites spent, the sites silent (the mark
    stays, asked later); None — stopped half-way (the mark stays)."""
    from core import parser_manga as PM, parser_manga_pages as PP
    item = PM.STORE.items().get(cand.key) or {}
    rec = PM.state_of(item, work)
    mark = dict(rec.get(AGAIN) or {})
    if mark.get("link"):                                # (v5.44.169) the owner's link of it: his word, not a check
        return _linked(settings, asker, cand, work, rec, mark, say=say, stop_check=stop_check, deadline=deadline)
    steps = tuple(step for step in PM.STEPS if step in (mark.get("steps") or PM.STEPS))
    memo: dict = {"wide": bool(mark.get("wide"))}
    verdict = None
    try:
        for step in steps:
            verdict = PM.check_work(asker, cand, work, steps=(step,), memo=memo, later_not=True, kept=mark)
            if verdict is not None or asker.stopped():
                break
    except PM.Pace as pace:
        _put_current(cand, work, rec, mark, {AGAIN: {**mark, "later_at": int(time.time() + max(60.0, pace.wait)),
                                                   "why": "очередь к сайтам манги"}})
        return {"state": "", "wait": round(pace.wait, 1)}
    if asker.stopped() or (verdict is not None and not verdict.state):
        return None
    if verdict is None:                                 # its files and its names said nothing (SauceNAO not asked)
        if memo.get("down"):
            verdict = PM.Verdict(PM.WAIT, "down", "сайты манги не ответили", later_at=int(time.time()) + PM.LATER,
                                 why="сайты манги не ответили")
        elif mark.get("sauce"):                         # (v5.44.169) a folder of the rules of before: SauceNAO knows
            verdict = PM.Verdict(PM.NOT, "later", PM.LATER_BY, sauce_at=int(time.time()),      # more now — asked later
                                 why="SauceNAO теперь спрашивается и про хентай-мангу")
        else:
            verdict = PM.Verdict(PM.NOT, "nothing", PP.not_words(memo.get("seen"), memo.get("late")))
    return settle(settings, asker, cand, work, rec, mark, verdict, say=say, stop_check=stop_check, deadline=deadline)


# ── (v5.44.169) the owner's link ────────────────────────────────────────────────────────────────────────────────────
def _by_hand(asker, work: "MD.Work", url: str) -> dict:
    """The title the owner's link names, opened (through the program's gates) and its pages compared with the
    folder's: {"verdict"} — it is there: his word confirms (the verdict's words say what the pages said);
    {"refused": words} — no such title, a gate closed it, the site will not give it; {"later": words} — the site did
    not answer."""
    from core import manga_clues as C, manhwa_sources as S, parser_manga as PM, parser_manga_nhentai as PN
    from core import parser_manga_pages as PP
    listed = [MD.pages_of(part) for part in work.parts]
    number = PN.number_of(url)
    if number:
        got = PN.by_link(asker, work, listed, number)
        if got.get("later") or got.get("refused"):
            return got
        pages = (f"страницы совпали ({got['alike']} из {got['local']})" if got["ok"] else
                 f"страницы папки и галереи не совпали ({got['alike']} из {got['local']}) — оставлено по вашей "
                 "ссылке" if got["local"] else "в папке нет страниц, которые можно сверить")
        return {"adult": True,
                "verdict": PM.Verdict(PM.MANGA, "hand", f"ваша ссылка → галерея nhentai «{got['title']}»: {pages}",
                                      title=got["title"], url=got["url"])}
    net = asker.net()
    try:
        chapter = C.mangadex_chapter(url)
        if chapter:                                     # one of MangaDex's chapters: its title
            mangadex = S.by_name("mangadex")
            url = mangadex.title_of_chapter(net, chapter) if mangadex is not None and mangadex.readable else ""
        source, key = S.source_for(url)
        if source is None:
            return {"refused": "по этой ссылке тайтла нет"}
        title = source.title(net, key)                  # (past the program's gate: ``manhwa_sources.checked``)
    except S.SourceError as exc:
        if asker.stopped() or S.transient(exc):
            return {"later": str(exc)}
        return {"refused": str(exc)}
    marks = PP.local_marks(work, listed)
    own = PP.chapter_of(work, marks[0][0]) if marks else ""
    got = PP._compare(asker, marks, title.url, own, exact=True, named=True,
                      limit=PM._limit(asker.settings)) if marks else {"error": "в папке нет страниц для сверки"}
    if asker.stopped():
        return {"later": "остановлено"}
    where = f"«{title.title}» на {source.label}"
    first = ""
    if got.get("ok"):
        pages, first = f"страницы совпали ({got['alike']} из {got['local']})", str(got.get("first") or "")
    elif got.get("error"):
        pages = f"страницы сверить не удалось ({str(got['error'])[:120]}) — оставлено по вашей ссылке"
    else:
        pages = (f"страницы папки и главы {got.get('first') or '1'} не совпали ({got['alike']} из {got['local']}) — "
                 "оставлено по вашей ссылке")
    if not first:                                       # which of its chapters the folder is: its own number, or
        first = own                                     # the title's only chapter
        if not first and len(work.parts) == 1:
            try:
                chapters = [c for c in source.chapters(net, title) if c.number]
            except S.SourceError:
                chapters = []
            if len(chapters) == 1:
                first = S._num(chapters[0].number)
    from core.manhwa_sites.ehentai import is_photo
    return {"adult": bool(title.is_erotic or source.adult_only),
            "verdict": PM.Verdict(PM.MANGA, "hand", f"ваша ссылка → тайтл {where}: {pages}", title=title.title,
                                  url=title.url, first=first, eh_photo=source.name == "ehentai" and is_photo(title))}


def _linked(settings: dict, asker, cand: "MD.Candidate", work: "MD.Work", rec: dict, mark: dict, *, say, stop_check,
            deadline: float) -> "dict | None":
    """A manga marked with the owner's link (:func:`say_link`), done: confirmed by it (:func:`settle`); the link
    refused — the mark goes, the row says why, nothing else changes; its site silent — asked again later
    (:data:`LINK_TRIES` times).  None — stopped half-way (the mark stays)."""
    from core import parser_manga as PM
    now = int(time.time())
    got = _by_hand(asker, work, str(mark.get("link") or ""))
    if asker.stopped() or not _current(cand, work, rec, mark):
        return None
    tries = int(mark.get("tries") or 0) + 1
    if got.get("later") and tries < LINK_TRIES:
        _put_current(cand, work, rec, mark, {AGAIN: {**mark, "tries": tries, "later_at": now + LINK_LATER,
                                                   "why": str(got["later"])[:200]}})
        return {"state": ""}
    refused = got.get("refused") or (f"сайт не отвечает ({got['later']}) — попробуйте позже" if got.get("later")
                                     else "")
    if refused:
        if not _put_current(cand, work, rec, mark, {AGAIN: {}, SAID: {"at": now, "was": str(mark.get("state") or ""),
                            "state": str(rec.get("state") or ""), "link": True, "refused": str(refused)[:300]}}):
            return None
        say(PM.line("МАНГА", f"«{work.name}» — ссылка не подошла: {refused}"))
        return {"state": str(rec.get("state") or ""), "kept": True}
    return settle(settings, asker, cand, work, rec, mark, got["verdict"], say=say, stop_check=stop_check,
                  deadline=deadline)


def settle(settings: dict, asker, cand: "MD.Candidate", work: "MD.Work", rec: dict, mark: dict, verdict, *, say,
           stop_check, deadline: float) -> dict:
    """What a second look said, done (the module's words): confirmed — as any confirmation (its cards of the gallery
    to the trash when its pages were parsed; another title — the copy's link moved); not confirmed — «не манга»
    still, or, a manga before, kept a manga (but for a gate that closes it now); the sites not to be asked — later."""
    from core import parser_manga as PM
    if not _current(cand, work, rec, mark):
        return {"state": "", "more": True}
    now = int(time.time())
    was = str(mark.get("state") or "")
    said = {"at": now, "was": was, "state": verdict.state, "by": verdict.by}
    if mark.get("link"):
        said["link"] = True                             # (v5.44.169) by the owner's link
    if verdict.state == PM.WAIT:
        later = int(verdict.later_at or 0) or now + PM.LATER
        _put_current(cand, work, rec, mark, {AGAIN: {**mark, "later_at": later, "why": verdict.why or verdict.by,
                                                   "late": dict(verdict.late or {})}})
        return {"state": ""}
    if verdict.state == PM.MANGA:
        moved = bool(was == PM.MANGA and mark.get("url") and verdict.url and mark["url"] != verdict.url)
        if moved and not mark.get("link") and rec.get("how") == "hand" and rec.get("copied"):
            # (v5.44.169) the title he linked himself: a second look names another one — his link stays
            if not _put_current(cand, work, rec, mark, {AGAIN: {}, SAID: {**said, "kept": True, "hand_kept": True,
                                                                       "other": str(verdict.title or "")}}):
                return {"state": "", "more": True}
            say(PM.line("МАНГА", f"«{work.name}» — перепроверка назвала другой тайтл («{verdict.title}»), но ссылку "
                                 "указали вы: оставлена ваша"))
            return {"state": PM.MANGA, "kept": True}
        extra = {AGAIN: {}, SAID: {**said, "moved": moved, "was_title": str(mark.get("title") or "")}}
        if was == PM.MANGA and rec.get("copied") and not verdict.eh_photo and _copy_folder(rec) is not None:
            return _confirmed_again(settings, cand, work, rec, verdict, extra, moved, say=say, stop_check=stop_check)
        if verdict.eh_photo:
            extra.update(copied=False, fill={}, had="", copy_error="", copy_retry_at=0, eh_queue_tries=0, eh_queued_at=0)
        if was == PM.NOT and rec.get("handed") and not verdict.eh_photo:
            extra["cards_to_trash"] = True              # its pages were parsed: their cards go («перенесено в мангу»)
        say(PM.line("МАНГА", f"«{work.name}» — " + ("" if mark.get("link") else "перепроверка: ")
                    + f"{verdict.by} → в «Мангу»"))
        return PM.apply(settings, asker, cand, work, verdict, say=say, stop_check=stop_check, deadline=deadline,
                        extra=extra, expected=rec)
    if was == PM.MANGA and verdict.how != "forbidden":  # a confirmation is not taken back by a second look
        if not _put_current(cand, work, rec, mark, {AGAIN: {}, SAID: {**said, "kept": True}}):
            return {"state": "", "more": True}
        say(PM.line("МАНГА", f"«{work.name}» — перепроверка не подтвердила ({verdict.by}): оставлена как была — в "
                             "«Манге»; если это не манга, скажите «Это не манга»"))
        return {"state": PM.MANGA, "kept": True}
    say(PM.line("НЕ МАНГА", f"«{work.name}» — перепроверка: {verdict.by}"))
    return PM.apply(settings, asker, cand, work, verdict, say=say, stop_check=stop_check, deadline=deadline,
                    # (a look that hands the folder on to SauceNAO has not said its word yet: v5.44.169)
                    extra={AGAIN: {}, SAID: {} if verdict.how == "later" else said}, expected=rec)


def _copy_folder(rec: dict) -> "Path | None":
    """The copy of a manga in the manga folder (its record of ``comic_manga``), when it is there."""
    from core import comic_manga as CM
    copy = CM.moved().get(str(rec.get("copy") or "")) if rec.get("copy") else None
    folder = Path(str(copy["folder"])) if isinstance(copy, dict) and copy.get("folder") else None
    return folder if folder is not None and folder.is_dir() else None


def _confirmed_again(settings: dict, cand, work, rec: dict, verdict, extra: dict, moved: bool, *, say,
                     stop_check) -> dict:
    """A manga in «Манга» confirmed again: what the look said kept; its copy is not copied anew (it is there) — its
    link asked again (the chapters the site has and the folder has not go into «Загрузки»), and moved when the title
    is another one now (the link «Из архива»'s way put on comes off with what it put, the new one goes on)."""
    from core import manga_archive as A, parser_manga as PM
    guard = PM._WorkGuard(cand, work, rec, stop_check)
    stop_check = guard.stopped
    folder = _copy_folder(rec)
    values = {**verdict.record(), "link_rule": PM.LINK_RULE, **extra}
    if not guard.put(values):
        return {"state": "", "more": True}
    out = {**rec, **values}
    if moved and folder is not None:
        with PM.STORE._lock:
            if stop_check():
                return {"state": "", "more": True}
            unlinked = A.unlink_folder(folder)
        if not unlinked:                               # (a folder the program downloaded itself: its link is its own)
            say(PM.line("МАНГА", f"«{work.name}» — перепроверка назвала другой тайтл («{verdict.title}»), но её "
                                 "папку программа скачала сама: привязка не меняется"))
            return out
        say(PM.line("МАНГА", f"«{work.name}» — " + ("" if extra[SAID].get("link") else "перепроверка: ")
                    + f"{verdict.by}; был «{extra[SAID].get('was_title')}» — описание, теги и главы теперь с этого "
                    "тайтла"))
    elif extra[SAID].get("link"):
        say(PM.line("МАНГА", f"«{work.name}» — {verdict.by}"))
    else:
        say(PM.line("МАНГА", f"«{work.name}» — перепроверка подтвердила: {verdict.by}"))
    if verdict.url and folder is not None:
        linked = PM.linked_values(settings, work, folder, out, say=say, stop_check=stop_check)
        if linked:
            if not guard.put(linked):
                return {"state": "", "more": True}
            out.update(linked)
    return out


# ── a change of the rules ───────────────────────────────────────────────────────────────────────────────────────────
def unset_copies(settings: dict, root: str) -> int:
    """(``parser_manga.background_round``) The copies of the manga folder whose folder is a сборка no more (a folder
    of a kind: ``manga_detect.named_after``): ``lb_set`` out of their ``info.json``, the mark of their record — once a
    candidate (``set_rule`` kept in it).  How many copies were taken out of a сборка."""
    from core import comic_manga as CM, manga_library as M, parser_manga as PM
    from core.file_safety import write_json_kept
    todo = []
    for key, item in PM.STORE.items(root).items():
        if int(item.get("set_rule") or 0) >= SET_RULE:
            continue
        cand = PM._cand(item)
        if cand is None:
            continue
        todo.append((key, cand, item))
    if not todo:
        return 0
    moved = CM.moved()
    changed, done = 0, []
    for key, cand, item in todo:
        ok = True
        if not cand.is_set:
            for work in cand.works:
                rec = (item.get("works") or {}).get(work.key) or {}
                copy_key = str(rec.get("copy") or f"root:{work.key}")
                copy = moved.get(copy_key)
                if not isinstance(copy, dict) or not copy.get("folder"):
                    continue
                info = Path(str(copy["folder"])) / "info.json"
                try:
                    data = json.loads(info.read_text(encoding="utf-8")) if info.is_file() else None
                    if isinstance(data, dict) and "lb_set" in data:
                        data.pop("lb_set", None)
                        write_json_kept(info, json.dumps(data, ensure_ascii=False, indent=1), dict)
                        changed += 1
                    if copy.get("set"):
                        CM._remember(copy_key, {"set": ""})
                except (OSError, ValueError) as exc:    # a file busy, a description damaged: asked again next round
                    _LOG.warning("parser manga: the сборка of %s not taken off its copy: %s", work.name, exc)
                    ok = False
        if ok:
            done.append(key)
    if done:
        def put(items: dict) -> None:
            for key in done:
                if key in items:
                    items[key]["set_rule"] = SET_RULE
        PM.STORE.change(put)
    if changed:
        M.LIBRARY.invalidate()
        _LOG.info("parser manga: %d copies are a сборка of their folder no more (a folder of a kind)", changed)
    return changed
