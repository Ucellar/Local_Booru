"""(v5.44.141) An account's favorites («лайки») of a booru site — downloaded into the gallery and parsed by themselves.

The owner, 9.11.2026: «на r34 люди часто лайкают посты что бы сохранить дак вот надо сделать хуйню которая с аккаунта
лайкнутые все скачивает и парсит само просто добавить кнопку через расширение прямо на странице профиля любого из
сайтов»; on the plan — «1 б 2 а»: parsed by the parser on every site, as his own files; new likes — by the button
«Докачать новые» only (nothing checks them by itself).

The browser extension (0.11.0) has the button on an account's page — its profile, its favorites — of rule34.xxx,
Gelbooru, Danbooru, ATF, e621/e926.  It leafs through the account's favorites in the owner's browser: the pages he could
open himself, one after another with pauses (the check of Cloudflare on rule34.xxx he passed in that browser — the
program never goes around it), and hands the posts' numbers here (``/extension/favorites``).  Then by itself:

1. :func:`take` — the numbers kept for the account (:data:`STORE`), the posts the library has (``site_posts``: a live
   card of that post) and the ones a gate closed before not asked again; the rest — one download (:func:`_start`):
   a subscription for one run over these numbers (``given_ids`` — ``subscriptions.run_subscription`` asks the site for
   the posts by :func:`posts_of`, then downloads them as a subscription does: originals, the site's tags, the
   blocklist, «Большая загрузка»), ``subscription_jobs`` of kind «favorites» — a row of «Скачать → Загрузки».  While
   subscriptions are being checked the download waits (:data:`STORE` «pending», :func:`background_round`).
2. :func:`posts_of` — the site's posts by their numbers, in packs the site understands (``site_posts._ask``); a
   post the program's gate closes (``gates.post``) is not downloaded.
3. :func:`_ended` (``subscription_jobs.ENDED``) — the files the download brought go to the parser
   (:func:`_start_parse`): a run over them (hard links in ``cache/favorites_parse`` with the parser's manifest of
   staged files, as «Брак → Искать снова» stages its files), skip_existing off — every site is asked by MD5 and its
   tags join the card; a file no site has stays the card it is (``parser_library_cards``: no Брак, no reverse search).
   While the parser is busy the files wait (:data:`STORE` «parse»); a run the person stopped — its files wait until
   the parser has done something else (``held``, :func:`_parse_over`); a run cut short by the program's closing goes
   again after its start.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from core import gates as G
from core.download_history import plural
from core.paths import SETTINGS_DIR

_LOG = logging.getLogger("local_booru.favorites")

STORE = SETTINGS_DIR / "favorites.json"
SITES = ("rule34.xxx", "gelbooru.com", "danbooru.donmai.us", "booru.allthefallen.moe", "e621.net", "e926.net")
KIND = "favorites"                   # the subscription job's kind and the parser run's
MAX_IDS = 200_000                    # numbers of one account kept / taken at once
SHOWN_IDS = 50_000                   # numbers the extension gets back (it stops at the posts known)
PARSE_DIR = "favorites_parse"        # <settings>/cache/<this>: the parser's staged files
SAY_EVERY = 200                      # posts asked of the site between two lines of the log
SITE_TRIES = 3                       # a site that does not answer: tries before the download gives up on the rest
POSTS = ("пост", "поста", "постов")

_LOCK = threading.RLock()
_PARSE_JOB = None                    # the parser's run over favorites this process started (its end is read from it)
_FIRST_ROUND = True                  # the first round after a start of the program: files held back go again


# ── the store ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _load() -> dict:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["accounts"] = data.get("accounts") if isinstance(data.get("accounts"), dict) else {}
    data["pending"] = [p for p in data.get("pending") or [] if isinstance(p, dict) and p.get("key")]
    data["parse"] = [str(p) for p in data.get("parse") or [] if p]
    data["held"] = [str(p) for p in data.get("held") or [] if p]
    data["parsing"] = data.get("parsing") if isinstance(data.get("parsing"), dict) else {}
    return data


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    part = STORE.with_name(STORE.name + ".tmp")
    part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(part, STORE)


def _numbers(values, limit: int = MAX_IDS) -> list:
    """Post numbers as the extension sent them: whole numbers > 0, each once, in their order."""
    out, seen = [], set()
    for value in values or ():
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in seen:
            seen.add(n)
            out.append(n)
            if len(out) >= limit:
                break
    return out


def site_of(site: str) -> str:
    """The site's name here and in ``site_posts`` ("" — not a site whose favorites the program downloads)."""
    from core.site_posts import canonical_site
    host = str(site or "").strip().lower()
    host = host[4:] if host.startswith("www.") else host
    return host if host in SITES else ("" if canonical_site(host) not in SITES else canonical_site(host))


def key_of(site: str, account: str) -> str:
    return f"{site}:{account}"


def _account(value) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() and len(text) <= 12 else ""


def library_posts(settings: dict, site: str, ids) -> tuple[set, set]:
    """(live, in the trash): the numbers of posts of ``site`` the library has a card of (``site_posts``).  A post whose
    card the person put into the trash is not downloaded again."""
    from core.database.connection import db
    from core.site_posts import canonical_site
    numbers = _numbers(ids)
    live: set = set()
    trash: set = set()
    if not numbers:
        return live, trash
    name = canonical_site(site)
    try:
        with db(settings, readonly=True) as con:
            for start in range(0, len(numbers), 800):
                part = numbers[start:start + 800]
                marks = ",".join("?" * len(part))
                for post_id, deleted in con.execute(
                        f"SELECT sp.post_id, MIN(i.deleted) FROM site_posts sp JOIN images i ON i.id = sp.image_id "
                        f"WHERE sp.site = ? AND sp.post_id IN ({marks}) GROUP BY sp.post_id", (name, *part)):
                    (live if int(deleted or 0) == 0 else trash).add(int(post_id))
    except Exception as exc:  # noqa: BLE001 - no site_posts: every post is asked (the download skips copies)
        _LOG.info("favorites: the library's posts of %s not read: %s", site, exc)
    return live, trash


# ── what the extension asks ──────────────────────────────────────────────────────────────────────────────────────────
def state(settings: dict, site: str, account: str) -> dict:
    """What the button shows for an account: downloaded before (the posts its favorites had then, how many of them the
    library has, when), a download of it going or waiting, files waiting for the parser.  ``ids`` — the numbers known
    (the extension stops leafing on Danbooru and e621 at a page of them: their favorites come newest first)."""
    host = site_of(site)
    who = _account(account)
    if not host or not who:
        return {"ok": False, "error": "site", "message": "Избранное этого сайта программа не скачивает"}
    key = key_of(host, who)
    with _LOCK:
        data = _load()
    rec = data["accounts"].get(key) or {}
    ids = _numbers(rec.get("ids"))
    out = {"ok": True, "site": host, "account": who, "name": str(rec.get("name") or ""), "known": len(ids),
           "ids": ids[:SHOWN_IDS], "last_at": int(rec.get("last_at") or 0), "got": int(rec.get("got") or 0),
           "have": len(library_posts(settings, host, ids)[0]) if ids else 0,
           "pending": any(p.get("key") == key for p in data["pending"]),
           "parse": len(data["parse"]) + len(data["held"]) + len((data["parsing"] or {}).get("files") or []),
           "going": None}
    job = _job()
    if job is not None and _job_key(job) == key:
        sub = job.subs[0] if job.subs else {}
        out["going"] = {"downloaded": int(job.downloaded or 0), "cards": int(sub.get("cards") or 0),
                        "status": str(job.status)}
    return out


def take(settings: dict, site: str, account: str, name: str, ids, *, mode: str = "all") -> dict:
    """The numbers of an account's favorites the extension collected: kept, and what the library lacks downloaded
    (now, or when the subscriptions being checked are done).  ``mode`` — «all» (every page) or «new» («Докачать
    новые»: the pages until the known ones)."""
    host = site_of(site)
    who = _account(account)
    if not host or not who:
        return {"ok": False, "error": "site", "message": "Избранное этого сайта программа не скачивает"}
    numbers = _numbers(ids)
    key = key_of(host, who)
    name = " ".join(str(name or "").split())[:80]
    now = int(time.time())
    with _LOCK:
        data = _load()
        rec = data["accounts"].setdefault(key, {"site": host, "account": who, "ids": [], "first_at": now})
        before = _numbers(rec.get("ids"))
        known = set(before)
        fresh = [n for n in numbers if n not in known]
        if mode == "new":
            rec["ids"] = (fresh + before)[:MAX_IDS]
        else:
            taken = set(numbers)
            rec["ids"] = (numbers + [n for n in before if n not in taken])[:MAX_IDS]
        rec.update(name=name or str(rec.get("name") or ""), last_at=now, runs=int(rec.get("runs") or 0) + 1)
        ruled = set(_numbers(rec.get("ruled")))
        _save(data)
    if not numbers:
        return {"ok": True, "total": 0, "have": 0, "todo": 0, "fresh": 0, "started": False,
                "message": "Новых лайков нет" if mode == "new" else "Сайт не показал ни одного поста в избранном"}
    have, trash = library_posts(settings, host, numbers)
    left_out = [n for n in numbers if n in ruled]
    todo = [n for n in numbers if n not in have and n not in trash and n not in ruled]
    out = {"ok": True, "site": host, "account": who, "total": len(numbers), "have": len(have), "trash": len(trash),
           "todo": len(todo), "fresh": len(fresh), "ruled": len(left_out), "started": False, "waiting": ""}
    head = f"Передано в Local Booru: {plural(len(numbers), POSTS)} — уже есть {len(have)}"
    head += (f", в корзине {len(trash)}" if trash else "") + (f", {G.name()} — не скачиваются {len(left_out)}"
                                                             if left_out else "")
    if not todo:
        out["message"] = head + ". Нового для скачивания нет"
        return out
    started, why = _start(settings, key, todo)
    out.update(started=started, waiting=why)
    head += f", скачиваются {len(todo)}"
    out["message"] = head + (". Ход — в «Скачать → Загрузки»" if started else f". Скачаются, когда {why}")
    _LOG.info("Избранное из расширения: %s (%s) — %d постов, есть %d, в корзине %d, к скачиванию %d%s", key, name,
              len(numbers), len(have), len(trash), len(todo), "" if started else " (ждут)")
    return out


# ── the download ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _job():
    from core import subscription_jobs as J
    job = J.current()
    return job if job is not None and job.kind == KIND and not job.finished_at else None


def _job_key(job) -> str:
    sub = job.subs[0] if getattr(job, "subs", None) else {}
    return str((sub.get("favorites") or {}).get("key") or "")


def _sub(key: str, rec: dict, todo: list) -> dict:
    host, who = str(rec.get("site") or ""), str(rec.get("account") or "")
    name = str(rec.get("name") or "") or f"аккаунт {who}"
    return {"id": "fav_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12], "name": f"Избранное: {name} — {host}",
            "query": f"fav:{name}", "sites": [{"site": host, "priority": 5}], "given_ids": {host: list(todo)},
            "max_pages": 1, "run_mode": "all", "run_direction": "newest_to_oldest", "one_off": True,
            "cards": len(todo), "favorites": {"key": key, "site": host, "account": who, "name": name}}


def _start(settings: dict, key: str, todo: list) -> tuple[bool, str]:
    """(started, why it waits): the download of ``todo`` of the account ``key`` — or kept until the subscriptions
    being checked are done."""
    from core import subscription_jobs as J
    register()
    with _LOCK:
        data = _load()
        rec = dict(data["accounts"].get(key) or {})
    sub = _sub(key, rec, todo)
    try:
        J.start(dict(settings), [sub], label=sub["name"], kind=KIND, forget_seeds=True)
    except J.SubscriptionsBusy as exc:
        with _LOCK:
            data = _load()
            data["pending"] = [p for p in data["pending"] if p.get("key") != key] + [
                {"key": key, "ids": list(todo), "at": int(time.time())}]
            _save(data)
        return False, _busy_words(str(exc))
    with _LOCK:
        data = _load()
        data["pending"] = [p for p in data["pending"] if p.get("key") != key]
        _save(data)
    return True, ""


def _busy_words(owner: str) -> str:
    text = str(owner or "")
    if "Избранное" in text:
        return "скачается избранное, которое уже идёт"
    if "закрыва" in text:
        return "программа запустится снова"
    if "Галерея" in text:
        return "закончится скачивание в галерею, которое идёт сейчас"
    return "закончится проверка подписок"


def _named(host: str, why) -> str:
    text = str(why or "").strip()
    return text if text.lower().startswith(host) else f"{host}: {text}"


def posts_of(settings: dict, site: str, ids, *, log=None, stop=None, key: str = "") -> list:
    """The site's raw posts of ``ids`` in their order, asked in packs the site understands (``site_posts``); a post the
    program's gate closes (``gates.post``) is not among them — said, and kept for the account (not
    asked again).  A site that does not answer: :data:`SITE_TRIES` tries, then what was got (the rest — «Докачать
    новые»); not one post got (the site switched off for want of its keys, refused) — the run's error, in words."""
    from core import site_posts as SP
    from core.parser.sites import SiteUnavailable
    from core.subscriptions import _extract_post_tags
    say = log or (lambda _m: None)
    numbers = _numbers(ids)
    host = site_of(site) or str(site or "")
    client = SP._Client(settings)
    remote = client.site(host)
    if remote is None:
        raise RuntimeError(f"{host}: сайта нет среди сайтов программы («Настройки → Парсер и сайты») — избранное "
                           "не спросить")
    if getattr(remote, "disabled_reason", ""):             # rule34.xxx without its keys and the like: said as the error
        raise RuntimeError(_named(host, remote.disabled_reason))
    st = {"style": "", "tried": [], "sure": [], "singles": False}
    pack = max(1, int(getattr(remote, "id_batch_limit", 0) or 1))
    found: dict = {}
    left, tries, said = list(numbers), 0, 0
    while left:
        if stop is not None and stop():
            say(f"  ИЗБРАННОЕ [{host}]: остановлено — спрошено {len(numbers) - len(left)} из {len(numbers)}")
            break
        part = left[:pack]
        try:
            got, _gone, later = SP._ask(remote, part, st)
        except SiteUnavailable as exc:
            tries += 1
            if not found and (tries >= SITE_TRIES or not getattr(exc, "transient", True)):
                raise RuntimeError(_named(host, getattr(remote, "disabled_reason", "") or exc)) from exc
            if tries >= SITE_TRIES or not getattr(exc, "transient", True):
                say(f"  ИЗБРАННОЕ [{host}]: сайт не отвечает ({exc}) — остальные {len(left)} постов не спрошены; "
                    "«Докачать новые» спросит их снова")
                break
            say(f"  ИЗБРАННОЕ [{host}]: сайт не ответил ({exc}) — ещё раз через {10 * tries} с")
            time.sleep(10 * tries)
            continue
        tries = 0
        found.update(got)
        left = list(later) + left[len(part):]
        asked = len(numbers) - len(left)
        if asked - said >= SAY_EVERY:
            said = asked
            say(f"  ИЗБРАННОЕ [{host}]: спрошено {asked} из {len(numbers)}, есть на сайте {len(found)}")
    posts, ruled, whys = [], [], {}
    for n in numbers:
        row = found.get(n)
        raw = row.get("_raw") if isinstance(row, dict) else None
        if not isinstance(raw, dict):
            continue
        why = G.post(host, _extract_post_tags(raw), raw.get("rating"))
        if why:                                    # a gate closed it: not downloaded, the words — the gate's
            ruled.append(n)
            whys[why] = whys.get(why, 0) + 1
            continue
        posts.append(raw)
    gone = len([n for n in numbers if n not in found]) - len(left)
    say(f"  ИЗБРАННОЕ [{host}]: постов {len(posts)} к скачиванию" + (f", нет на сайте {gone}" if gone > 0 else ""))
    if ruled:
        for why, count in whys.items():
            say(f"  ИЗБРАННОЕ: не скачиваю постов — {count}: {why}")
        if key:
            with _LOCK:
                data = _load()
                rec = data["accounts"].get(key)
                if isinstance(rec, dict):
                    rec["ruled"] = _numbers(list(rec.get("ruled") or []) + ruled)
                    _save(data)
    return posts


def _ended(job) -> None:
    """(``subscription_jobs.ENDED``) A download of favorites is over: its count kept, the files it brought — to the
    parser (and the files a stopped run of the parser held back — with them)."""
    if getattr(job, "kind", "") != KIND:
        return
    from core.settings import load_settings
    settings = load_settings()
    key = _job_key(job)
    files = [str(p) for p in getattr(job, "files", None) or [] if p]
    with _LOCK:
        data = _load()
        rec = data["accounts"].get(key)
        if isinstance(rec, dict):
            rec["got"] = int(rec.get("got") or 0) + int(getattr(job, "downloaded", 0) or 0)
        if data["parsing"]:
            _parse_over(settings, data)              # a run over earlier files ended meanwhile: they go where they belong
        if files or data["held"]:
            data["parse"] = list(dict.fromkeys(data["held"] + data["parse"] + files))
            data["held"] = []
        _save(data)
    if data["parse"]:
        try:
            _start_parse(settings)
        except Exception as exc:  # noqa: BLE001 - the files wait: the next round starts the parser
            _LOG.warning("favorites: the parser not started: %s: %s", type(exc).__name__, exc)


# ── the parser ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _stage_root(settings: dict) -> Path:
    from core.paths import suggested_settings_storage_dir
    return suggested_settings_storage_dir(settings) / "cache" / PARSE_DIR


def _stage(settings: dict, files: list) -> Path:
    """The parser's folder of ``files``: hard links (copies on another disk) and the manifest that names each one's
    card — the parser's staged files (``runner._load_retry_manifest``), as «Брак → Искать снова» stages its own."""
    from core.parser.runner import RETRY_MANIFEST
    root = _stage_root(settings)
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    items, used = [], set()
    for raw in files:
        src = Path(raw)
        name, n = src.name or "file", 2
        while name.lower() in used:
            name = f"{src.stem}__{n}{src.suffix}"
            n += 1
        used.add(name.lower())
        dst = root / name
        try:
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        except OSError as exc:
            _LOG.info("favorites: %s not staged for the parser: %s", src.name, exc)
            continue
        items.append({"queue_file": str(dst), "source_file": str(src), "original_path": str(src),
                      "media_path": str(src)})
    (root / RETRY_MANIFEST).write_text(json.dumps({"created_at": int(time.time()), "filter": "favorites",
                                                   "count": len(items), "items": items, "skipped": []},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
    return root


def run_settings(settings: dict, root: Path) -> dict:
    """The settings of the parser's run over the downloaded favorites: every site asked by MD5 (not only for files
    without tags), a file no site has stays the card it is (``parser_library_cards``: no Брак, no reverse search)."""
    out = dict(settings)
    out.update(root=str(root), skip_existing=False, tag_only_untagged=False, retry_nomatch=False,
               parser_skip_manga=False, limit_files=0, parser_recheck_new_sites=False, skip_copy_suffix_files=False,
               parser_library_cards=True)
    out.pop("_parser_explicit_paths", None)
    return out


def _start_parse(settings: dict) -> bool:
    """The parser over the files the downloads of favorites brought, when it is free (True — started)."""
    global _PARSE_JOB
    from core.parser import jobs as P
    if P.is_running():
        return False
    with _LOCK:
        data = _load()
        if data["parsing"]:
            if not _parse_over(settings, data):      # the run over earlier files is still going
                return False
            _save(data)
        files = [f for f in data["parse"] if Path(f).is_file()]
        if not files:
            if data["parse"]:
                data["parse"] = []
                _save(data)
            return False
        root = _stage(settings, files)
        try:
            job = P.start(run_settings(settings, root), label=f"Избранное: теги со всех сайтов ({len(files)})",
                          kind=KIND)
        except P.ParserBusy:
            return False
        _PARSE_JOB = job
        data["parsing"] = {"job": int(job.id), "files": files, "at": int(time.time())}
        taken = set(files)
        data["parse"] = [f for f in data["parse"] if f not in taken]
        _save(data)
    _LOG.info("Избранное: парсер по всем сайтам — %d файлов", len(files))
    return True


def _parse_over(settings: dict, data: dict) -> bool:
    """The end of the parser's run over favorites (False — it is still going).  Done — forgotten; stopped or failed in
    this run of the program — its files held back (``held``) until the parser has done something else, a download of
    favorites ends or the program starts again (not again and again a minute later); cut short by the program's
    closing (another start of the program) — again."""
    parsing = data["parsing"]
    job = _PARSE_JOB if _PARSE_JOB is not None and int(_PARSE_JOB.id) == int(parsing.get("job") or 0) else None
    if job is not None and not job.finished_at:
        return False
    files = [str(f) for f in parsing.get("files") or []]
    if job is None:
        data["parse"] = list(dict.fromkeys(files + data["parse"]))
    elif job.status != "done" or (job.summary or {}).get("stopped"):
        data["held"] = list(dict.fromkeys(data["held"] + files))
    else:
        shutil.rmtree(_stage_root(settings), ignore_errors=True)
    data["parsing"] = {}
    return True


def background_round(settings: dict) -> dict:
    """(the duty «favorites», every minute) The waiting downloads when the subscriptions are free; the end of the
    parser's run; the files waiting for the parser when it is free."""
    global _FIRST_ROUND
    from core import subscription_jobs as J
    from core.parser import jobs as P
    out = {"started": 0, "parse": False}
    with _LOCK:
        data = _load()
    if data["pending"] and not J.is_running():
        first = data["pending"][0]
        started, _why = _start(settings, str(first.get("key")), _numbers(first.get("ids")))
        out["started"] = int(started)
    with _LOCK:
        data = _load()
        changed = False
        if data["parsing"]:
            if not _parse_over(settings, data):
                return out
            changed = True
        last = P.current()
        if data["held"] and (_FIRST_ROUND or (last is not None and last is not _PARSE_JOB and last.finished_at)):
            data["parse"] = list(dict.fromkeys(data["held"] + data["parse"]))
            data["held"] = []
            changed = True
        _FIRST_ROUND = False
        if changed:
            _save(data)
    if data["parse"] and _job() is None and not P.is_running():
        out["parse"] = _start_parse(settings)
    return out


def register() -> None:
    """The hook of the subscriptions' jobs (once)."""
    from core import subscription_jobs as J
    if _ended not in J.ENDED:
        J.ENDED.append(_ended)
