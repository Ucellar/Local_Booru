"""e621's daily export of its posts as a local MD5 index (v5.44.25).

e621's API help: «If you are looking for bulk data, or are expecting to do tens of thousands of lookups, please use the
daily data exports available at https://e621.net/db_export/».  The owner (1.11.2026), offered it: «давай».

With «Сайты и ключи» → «Парсер» → «MD5 на e621 — по его выгрузке» (``e621_export_enabled``) on, the program downloads
e621's export of posts — ``posts.csv.gz``, about 2 GB; https://e621.net/db_exports.json names it, its size and SHA-256 —
into ``CACHE_DIR/e621_export`` (the download resumes where it stopped, it is checked by its SHA-256), keeps of it only
MD5 → post number (the index, a few hundred MB; the export itself is let go), and the e621 site answers its MD5 searches
from it (``core/parser/sites/e621.py``): a post the index knows is asked of the API by its number, a hundred in a
request — its tags are fresh; a deleted one is «not on e621», as e621's own MD5 search says; an MD5 the index does not
know is «not on e621» without a request while the index is current.

Current: the posts uploaded after the export are asked of the API every :data:`CATCH_UP_EVERY` seconds while the
program runs (``page=a<the last number>``, 320 in a request — e621's pace: the requests go through the program's
limiter); an index not caught up within :data:`FRESH_HOURS` answers only what it knows, the rest is asked by MD5 as
before.  The export is downloaded again every ``e621_export_refresh_days`` (30): it knows the posts whose file was
replaced and the ones deleted since.  The work goes in a thread of its own (``tick`` — from
``core/background_duties.py`` every 10 minutes, and from a search when the index is behind); it stops at the exit and
when the switch is turned off, and goes on from where it was.  Nothing of it is sent anywhere: e621 is only asked.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

EXPORTS_URL = "https://e621.net/db_exports.json"
POSTS_URL = "https://e621.net/posts.json"
CATCH_UP_EVERY = 3600          # seconds between two catch-ups of the posts uploaded after the export
FRESH_HOURS = 6                # an index caught up within this long says «not on e621» by itself
RETRY_AFTER = 3600             # seconds after a failure before the work is tried again
CHECK_EVERY = 6 * 3600         # seconds between two looks at db_exports.json when the export is due
CATCH_UP_PAGES = 400           # pages of a catch-up at most (320 posts each — more than a month of e621's uploads)
PAGE = 320
CHUNK = 1024 * 1024
BATCH = 20000
SPARE = 1536 * 1024 * 1024     # bytes the disk keeps free beyond the export
DELETED, PENDING = 1, 2
_HOSTS = ("e621.net", "static1.e621.net")

_LOCK = threading.Lock()
_STATE: dict = {"phase": "idle", "done": 0, "total": 0, "rows": 0, "note": ""}
_WORKER: threading.Thread | None = None
_LIVE: dict = {"get": None}    # the program's live settings (``tick`` of the duties gives it): the switch read anew


@dataclass
class Answer:
    known: dict = field(default_factory=dict)      # md5 -> (post number, flags)
    current: bool = False                          # caught up within FRESH_HOURS: an unknown MD5 is not on e621
    export_date: str = ""
    rows: int = 0


# ── where it lives ──────────────────────────────────────────────────────────
def root() -> Path:
    from core.paths import CACHE_DIR
    return Path(CACHE_DIR) / "e621_export"


def _pointer() -> Path:
    return root() / "current.json"


def _state_file() -> Path:
    return root() / "state.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    from core.file_safety import atomic_write_bytes
    atomic_write_bytes(path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))


def current_db() -> Path | None:
    name = str(_read_json(_pointer()).get("db") or "")
    path = root() / name if name and "/" not in name and "\\" not in name else None
    return path if path is not None and path.is_file() else None


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _meta(con) -> dict:
    return {str(r["key"]): str(r["value"]) for r in con.execute("SELECT key, value FROM meta")}


# ── the switch ──────────────────────────────────────────────────────────────
def enabled(settings) -> bool:
    """On, and e621 is one of the sites."""
    s = settings or {}
    row = (s.get("sites") or {}).get("e621.net") if isinstance(s.get("sites"), dict) else None
    return bool(s.get("e621_export_enabled", False)) and isinstance(row, dict) and bool(row.get("enabled", True))


def _refresh_days(settings) -> int:
    try:
        return max(1, min(365, int((settings or {}).get("e621_export_refresh_days", 30) or 30)))
    except (TypeError, ValueError):
        return 30


# ── what the e621 site asks ─────────────────────────────────────────────────
def lookup(settings, md5s) -> Answer | None:
    """What the index knows of these MD5s, or None — it cannot answer (off, not made yet).  An index behind its
    time starts its catch-up (``tick``)."""
    if not enabled(settings):
        return None
    path = current_db()
    if path is None:
        tick(settings)
        return None
    wanted = []
    for m in md5s or []:
        try:
            wanted.append(bytes.fromhex(str(m)))
        except ValueError:
            continue
    try:
        con = _connect(path)
    except sqlite3.Error:
        return None
    try:
        meta = _meta(con)
        known = {}
        for i in range(0, len(wanted), 500):
            part = wanted[i:i + 500]
            marks = ",".join("?" * len(part))
            for row in con.execute(f"SELECT md5, post_id, flags FROM md5 WHERE md5 IN ({marks})", part):
                known[bytes(row["md5"]).hex()] = (int(row["post_id"]), int(row["flags"]))
    except sqlite3.Error:
        return None
    finally:
        con.close()
    caught = float(meta.get("caught_up_at") or 0)
    current = bool(caught) and time.time() - caught < FRESH_HOURS * 3600
    if not current:
        tick(settings)
    return Answer(known=known, current=current, export_date=meta.get("export_date", ""),
                  rows=int(meta.get("rows") or 0))


# ── its state for the page ──────────────────────────────────────────────────
def _set(**kw) -> None:
    with _LOCK:
        _STATE.update(kw)


def status(settings) -> dict:
    """{"on", "phase", "text"} — what «Сайты и ключи» says about it."""
    on = bool((settings or {}).get("e621_export_enabled", False))
    with _LOCK:
        state = dict(_STATE)
        running = _WORKER is not None and _WORKER.is_alive()
    info: dict = {}
    path = current_db()
    if path is not None:
        try:
            con = _connect(path)
            try:
                info = _meta(con)
            finally:
                con.close()
        except sqlite3.Error:
            info = {}
    have = ""
    if info:
        caught = float(info.get("caught_up_at") or 0)
        have = (f"выгрузка от {as_day(info.get('export_date', ''))}: {as_count(int(info.get('rows') or 0))} постов" +
                (f"; новые посты догружены {time.strftime('%d.%m %H:%M', time.localtime(caught))}" if caught else ""))
    if not on:
        return {"on": False, "phase": "off", "text": "выключено" + (f" ({have} — лежит на диске)" if have else "")}
    phase = state.get("phase") if running else "idle"
    if phase == "downloading":
        total = int(state.get("total") or 0)
        done = int(state.get("done") or 0)
        pct = f"{done * 100 // total} % — " if total else ""
        text = f"скачивается выгрузка e621: {pct}{_size(done)} из {_size(total)}"
    elif phase == "building":
        text = f"составляется указатель MD5: {as_count(int(state.get('rows') or 0))} постов из выгрузки"
    elif phase == "catching_up":
        text = f"догружаются новые посты e621: {as_count(int(state.get('rows') or 0))}"
    elif phase == "checking":
        text = "спрашиваю e621, какая выгрузка свежая"
    else:
        failed = _read_json(_state_file())
        if failed.get("error") and time.time() - float(failed.get("failed_at") or 0) < RETRY_AFTER:
            text = f"не получилось: {failed['error']} — попробую снова через час"
        elif have:
            text = have
        else:
            text = "скачается в ближайшие минуты (около 2 ГБ, на диск с архивом)"
    if have and phase in ("downloading", "building", "checking"):
        text += f"; пока работает {have}"
    return {"on": True, "phase": phase or "idle", "text": text}


def as_day(stamp: str) -> str:
    stamp = str(stamp or "")
    return f"{stamp[8:10]}.{stamp[5:7]}.{stamp[0:4]}" if len(stamp) >= 10 else (stamp or "?")


def as_count(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def _size(n: int) -> str:
    n = int(n or 0)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} ГБ".replace(".", ",")
    return f"{n // (1024 ** 2)} МБ" if n >= 1024 ** 2 else f"{max(1, n // 1024) if n else 0} КБ"


# ── the work ────────────────────────────────────────────────────────────────
def tick(settings, live=None) -> bool:
    """Start the work when something is due (the index missing or old, the new posts not caught up); True — it was
    started.  Never waits.  ``live`` — what returns the program's live settings (the work stops when the switch is
    turned off)."""
    global _WORKER
    if live is not None:
        _LIVE["get"] = live
    if not enabled(settings):
        return False
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return False
        job = _due(settings)
        if not job:
            return False
        work = dict(settings)
        _WORKER = threading.Thread(target=_run, args=(work, job), name="e621-export", daemon=True)
        _STATE.update({"phase": "checking" if job == "refresh" else "catching_up", "done": 0, "total": 0, "rows": 0})
        _WORKER.start()
        return True


def wait(timeout: float | None = None) -> None:
    """Wait for the work started (the checks)."""
    worker = _WORKER
    if worker is not None:
        worker.join(timeout)


def _due(settings) -> str:
    """"refresh", "catch_up" or "" (nothing to do now)."""
    state = _read_json(_state_file())
    now = time.time()
    if state.get("error") and now - float(state.get("failed_at") or 0) < float(state.get("retry") or RETRY_AFTER):
        return ""
    path = current_db()
    meta: dict = {}
    if path is not None:
        try:
            con = _connect(path)
            try:
                meta = _meta(con)
            finally:
                con.close()
        except sqlite3.Error:
            meta = {}
    if not meta:
        return "refresh"
    built = float(meta.get("built_at") or 0)
    if now - built > _refresh_days(settings) * 86400 and now - float(state.get("checked_at") or 0) > CHECK_EVERY:
        return "refresh"
    if now - float(meta.get("caught_up_at") or 0) > CATCH_UP_EVERY:
        return "catch_up"
    return ""


def _stopping() -> bool:
    """The exit, or the switch turned off."""
    from core.shutdown import is_shutting_down
    if is_shutting_down():
        return True
    get = _LIVE["get"]
    if get is None:
        return False
    try:
        return not bool((get() or {}).get("e621_export_enabled", False))
    except Exception:  # the live settings cannot be read: the work goes on
        return False


class _Stopped(Exception):
    pass


class _Failed(Exception):
    """A failure said on the page; the work is tried again in ``retry`` seconds."""

    def __init__(self, message: str, *, retry: float = RETRY_AFTER):
        super().__init__(message)
        self.retry = float(retry)


def _run(settings: dict, job: str) -> None:
    from core.parser.http import HttpClient
    import logging
    log = logging.getLogger("local_booru.e621_export")
    stop_flag = {"at": 0.0, "value": False}

    def stop() -> bool:
        now = time.monotonic()
        if now - stop_flag["at"] > 2:            # the switch read anew every 2 s, not every chunk
            stop_flag["at"], stop_flag["value"] = now, _stopping()
        return stop_flag["value"]

    http = HttpClient(settings, log=lambda m: log.info("%s", m), cancel_check=stop, pause_wait=lambda: None)
    try:
        root().mkdir(parents=True, exist_ok=True)
        _tidy()
        if job == "refresh":
            _refresh(settings, http, stop)
        _catch_up(settings, http, stop)
        state = _read_json(_state_file())
        for key in ("error", "failed_at", "retry"):
            state.pop(key, None)
        _write_json(_state_file(), state)
    except _Stopped:
        log.info("e621 export: stopped, goes on next time")
    except Exception as exc:  # said on the page; tried again in an hour
        from core.redaction import sanitize_text
        reason = sanitize_text(f"{exc}" if isinstance(exc, _Failed) else f"{type(exc).__name__}: {exc}")[:300]
        log.warning("e621 export failed: %s", reason)
        state = _read_json(_state_file())
        state.update({"error": reason, "failed_at": time.time(),
                      "retry": exc.retry if isinstance(exc, _Failed) else RETRY_AFTER})
        try:
            _write_json(_state_file(), state)
        except OSError:
            pass
    finally:
        http.close_all()
        _set(phase="idle")


def _tidy() -> None:
    """What an earlier start left: unfinished indexes, indexes no longer used (one in use by a reader is let go next
    time)."""
    keep = current_db()
    for path in root().glob("md5-*.sqlite*"):
        if keep is not None and path.name.startswith(keep.name):
            continue
        try:
            path.unlink()
        except OSError:
            pass


def _get_json(http, url: str, params=None, settings=None):
    from core.parser.http import HttpFailure
    from core.parser.util import json_body
    headers = {"Accept": "application/json"}
    login = str(((settings or {}).get("sites") or {}).get("e621.net", {}).get("login") or "").strip()
    key = str(((settings or {}).get("sites") or {}).get("e621.net", {}).get("api_key") or "").strip()
    try:
        response = http.get(url, params=params, headers=headers, auth=(login, key) if login and key else None)
    except HttpFailure as exc:
        raise _Failed(f"сеть: {exc.detail}", retry=600) from exc
    if response.status_code != 200:
        raise _Failed(f"e621 ответил HTTP {response.status_code}")
    data = json_body(response)
    if data is None:
        raise _Failed("e621 ответил не JSON")
    return data


def _refresh(settings: dict, http, stop) -> None:
    from core.parser.http import Cancelled
    _set(phase="checking")
    try:
        exports = _get_json(http, EXPORTS_URL, settings=settings)
    except Cancelled as exc:
        raise _Stopped() from exc
    row = next((x for x in exports if isinstance(x, dict) and x.get("name") == "posts"), None) \
        if isinstance(exports, list) else None
    if row is None:
        raise _Failed("в списке выгрузок e621 нет выгрузки постов")
    url = str(row.get("url") or "")
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "") not in _HOSTS:
        raise _Failed(f"выгрузка не на сервере e621 ({parts.hostname or url[:60]}) — не скачиваю")
    size = int(row.get("file_size") or 0)
    checksum = str(row.get("checksum") or "").lower()
    updated = str(row.get("updated_at") or "")
    state = _read_json(_state_file())
    state["checked_at"] = time.time()
    _write_json(_state_file(), state)
    path = current_db()
    if path is not None:
        con = _connect(path)
        try:
            have = _meta(con)
        finally:
            con.close()
        if have.get("export_sha256") == checksum and checksum:
            con = sqlite3.connect(str(path), timeout=10)
            try:                                  # the same export as the index: it counts as made now
                with con:
                    con.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (str(time.time()),))
            finally:
                con.close()
            return
    gz = _download(settings, http, stop, url=url, size=size, checksum=checksum)
    _build(gz, stop, export_date=updated, size=size, checksum=checksum)
    try:
        gz.unlink()
    except OSError:
        pass
    _tidy()


def _download(settings: dict, http, stop, *, url: str, size: int, checksum: str) -> Path:
    from core.http_rate_limiter import RequestCancelled, wait_for
    from core.network import assert_url_allowed
    import requests
    part = root() / "posts.csv.gz.part"
    note = root() / "posts.csv.gz.part.json"
    done_path = root() / "posts.csv.gz"
    if done_path.is_file() and _read_json(note).get("checksum") == checksum and done_path.stat().st_size == size:
        return done_path
    if _read_json(note).get("checksum") != checksum:
        for stale in (part, done_path):
            try:
                stale.unlink()
            except OSError:
                pass
        _write_json(note, {"url": url, "size": size, "checksum": checksum})
    have = part.stat().st_size if part.is_file() else 0
    if size and have > size:
        part.unlink()
        have = 0
    free = shutil.disk_usage(root()).free
    if size and free < size - have + SPARE:
        raise _Failed(f"мало места на диске: нужно {_size(size - have + SPARE)}, свободно {_size(free)}")
    digest = hashlib.sha256()
    if have:
        with part.open("rb") as fh:
            for block in iter(lambda: fh.read(CHUNK), b""):
                digest.update(block)
                if stop():
                    raise _Stopped()
    assert_url_allowed(url)
    session = http.session("static1.e621.net")
    headers = {"Accept": "application/octet-stream"}
    etag = str(_read_json(note).get("etag") or "")
    if have:
        headers["Range"] = f"bytes={have}-"
        if etag:
            headers["If-Range"] = etag
    try:
        wait_for(url, settings)
    except RequestCancelled as exc:
        raise _Stopped() from exc
    _set(phase="downloading", done=have, total=size)
    try:
        response = session.get(url, headers=headers, stream=True, timeout=(15, 120))
    except requests.RequestException as exc:
        raise _Failed(f"сеть: {type(exc).__name__}", retry=600) from exc
    try:
        if response.status_code == 200 and have:          # the server sends it all again: from the start
            have = 0
            digest = hashlib.sha256()
            mode = "wb"
        elif response.status_code == 206 and have:
            mode = "ab"
        elif response.status_code == 200:
            mode = "wb"
        else:
            raise _Failed(f"сервер e621 ответил HTTP {response.status_code} на скачивание выгрузки")
        tag = str(response.headers.get("ETag") or "")
        if tag and tag != etag:
            info = _read_json(note)
            info["etag"] = tag
            _write_json(note, info)
        done = have
        with part.open(mode) as fh:
            for block in response.iter_content(CHUNK):
                if not block:
                    continue
                fh.write(block)
                digest.update(block)
                done += len(block)
                _set(done=done)
                if stop():
                    raise _Stopped()
    except requests.RequestException as exc:
        raise _Failed(f"связь оборвалась на {_size(part.stat().st_size if part.is_file() else 0)} — продолжу "
                      f"с того же места ({type(exc).__name__})", retry=600) from exc
    finally:
        response.close()
    got = part.stat().st_size
    if size and got != size:
        raise _Failed(f"скачано {_size(got)} из {_size(size)} — продолжу с того же места", retry=600)
    if checksum and digest.hexdigest() != checksum:
        for bad in (part, note):
            try:
                bad.unlink()
            except OSError:
                pass
        raise _Failed("скачанная выгрузка не совпала по SHA-256 — скачаю заново")
    part.replace(done_path)
    return done_path


def _build(gz: Path, stop, *, export_date: str, size: int, checksum: str) -> None:
    """MD5 → post number of every row of the export, in a new index; it becomes the current one when it is whole."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = root() / f"md5-{stamp}.sqlite"
    tmp = root() / f"md5-{stamp}.sqlite.tmp"
    try:
        tmp.unlink()
    except OSError:
        pass
    csv.field_size_limit(2 ** 31 - 1)
    _set(phase="building", rows=0, done=0, total=gz.stat().st_size)
    con = sqlite3.connect(str(tmp))
    rows = 0
    top = 0
    try:
        con.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-131072;"
                          "CREATE TABLE md5 (md5 BLOB PRIMARY KEY, post_id INTEGER NOT NULL, flags INTEGER NOT NULL)"
                          " WITHOUT ROWID; CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
        put = ("INSERT INTO md5 VALUES (?,?,?) ON CONFLICT(md5) DO UPDATE SET post_id=excluded.post_id, "
               "flags=excluded.flags WHERE excluded.flags <= md5.flags")
        with gz.open("rb") as raw:
            text = io.TextIOWrapper(gzip.GzipFile(fileobj=raw), encoding="utf-8", errors="replace", newline="")
            reader = csv.reader(text)
            header = next(reader, None) or []
            try:
                at = {name: header.index(name) for name in ("id", "md5", "is_deleted", "is_pending")}
            except ValueError as exc:
                raise _Failed(f"в выгрузке e621 другие столбцы ({', '.join(header[:6])}…)") from exc
            batch = []
            for rec in reader:
                try:
                    md5 = bytes.fromhex(rec[at["md5"]])
                    post = int(rec[at["id"]])
                except (IndexError, ValueError):
                    continue
                if len(md5) != 16:
                    continue
                flags = (DELETED if rec[at["is_deleted"]] == "t" else 0) | (PENDING if rec[at["is_pending"]] == "t" else 0)
                batch.append((md5, post, flags))
                top = max(top, post)
                if len(batch) >= BATCH:
                    with con:
                        con.executemany(put, batch)
                    rows += len(batch)
                    batch.clear()
                    _set(rows=rows, done=raw.tell())
                    if stop():
                        raise _Stopped()
            if batch:
                with con:
                    con.executemany(put, batch)
                rows += len(batch)
        meta = {"export_date": export_date, "export_size": str(size), "export_sha256": checksum, "rows": str(rows),
                "max_id": str(top), "built_at": str(time.time()), "caught_up_at": "0", "caught_up_id": str(top)}
        with con:
            con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", list(meta.items()))
        con.execute("PRAGMA journal_mode=WAL")
    except BaseException:
        con.close()
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    con.close()
    if rows == 0:
        raise _Failed("в выгрузке e621 не нашлось ни одного поста")
    tmp.replace(final)
    _write_json(_pointer(), {"db": final.name, "export_date": export_date, "rows": rows, "built_at": time.time()})
    _set(rows=rows)


def _catch_up(settings: dict, http, stop) -> None:
    """The posts uploaded after the export (and after the last catch-up), page after page."""
    from core.parser.http import Cancelled
    path = current_db()
    if path is None:
        return
    con = sqlite3.connect(str(path), timeout=30)
    try:
        meta = _meta_plain(con)
        last = int(meta.get("caught_up_id") or meta.get("max_id") or 0)
        added = 0
        whole = False                      # a short page came: no post after it yet — caught up
        _set(phase="catching_up", rows=0)
        for _page in range(CATCH_UP_PAGES):
            if stop():
                raise _Stopped()
            try:
                data = _get_json(http, POSTS_URL, params={"limit": PAGE, "page": f"a{last}"}, settings=settings)
            except Cancelled as exc:
                raise _Stopped() from exc
            posts = data.get("posts") if isinstance(data, dict) else data
            rows = []
            for raw in posts if isinstance(posts, list) else []:
                if not isinstance(raw, dict):
                    continue
                try:
                    post = int(raw.get("id"))
                    md5 = bytes.fromhex(str((raw.get("file") or {}).get("md5") or ""))
                except (TypeError, ValueError):
                    continue
                flags = raw.get("flags") if isinstance(raw.get("flags"), dict) else {}
                mark = (DELETED if flags.get("deleted") else 0) | (PENDING if flags.get("pending") else 0)
                if len(md5) == 16:
                    rows.append((md5, post, mark))
                last = max(last, post)
            with con:
                con.executemany("INSERT INTO md5 VALUES (?,?,?) ON CONFLICT(md5) DO UPDATE SET "
                                "post_id=excluded.post_id, flags=excluded.flags", rows)
                con.execute("INSERT OR REPLACE INTO meta VALUES ('caught_up_id', ?)", (str(last),))
            added += len(rows)
            _set(rows=added)
            if not isinstance(posts, list) or len(posts) < PAGE:
                whole = True
                break
        with con:                          # not caught up to the end (the cap): current it is not — the next goes on
            if whole:
                con.execute("INSERT OR REPLACE INTO meta VALUES ('caught_up_at', ?)", (str(time.time()),))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('rows', ?)",
                        (str(int(meta.get("rows") or 0) + added),))
    finally:
        con.close()


def _meta_plain(con) -> dict:
    return {str(k): str(v) for k, v in con.execute("SELECT key, value FROM meta")}
