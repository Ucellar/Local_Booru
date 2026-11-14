"""Recognized E-Hentai photo folders download the complete gallery into Manga; originals stay untouched."""
from __future__ import annotations

import json
import time
from pathlib import Path

POLL_AFTER = 30
OFF_AFTER = 60


def have(folder: Path, key: str) -> dict:
    """Only a complete chapter with this exact source ID; a local «Глава 1» is not proof of a whole gallery."""
    from core import manhwa_download as D
    chapter = D.had(folder, "ehentai").get(("id", key))
    if chapter is None or not Path(chapter).is_dir():
        return {}
    chapter = Path(chapter)
    try:
        meta = chapter / "chapter.json"
        if meta.stat().st_size > 4 * 1024 * 1024:
            return {}
        data = json.loads(meta.read_text(encoding="utf-8"))
        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list) or not 1 <= len(pages) <= 2000:
            return {}
        seen = set()
        for page in pages:
            name = page.get("file") if isinstance(page, dict) else None
            if not isinstance(name, str) or not name or Path(name).name != name or \
                    "/" in name or "\\" in name or name in seen:
                return {}
            path = chapter / name
            if path.suffix.lower() not in D.TYPES.values() or not path.is_file():
                return {}
            with path.open("rb") as stream:
                if not D._picture_type(stream.read(16)):
                    return {}
            seen.add(name)
    except (OSError, ValueError):
        return {}
    return {("id", key): chapter}


def complete(settings: dict, url: str):
    """(library kind, exact linked folder) only after its real chapter is complete; no network requests."""
    from core import manhwa_download as D, manhwa_sources as S
    source, key = S.source_for(str(url or ""))
    if source is None or source.name != "ehentai" or not key:
        return None
    linked = D.linked_folder(settings, source.name, key)
    return linked if linked and have(Path(linked[1]), key) else None


def _going(source, key: str, url: str) -> bool:
    from core import manhwa_queue as Q
    if any(Q.queued(url, where) for where in ("manga", "", "manhwa")):
        return True
    current = Q.current_record() or {}
    if source.key_of(str(current.get("url") or "")) == key:
        return True
    return any(source.key_of(str(item.get("url") or "")) == key for item in Q.public())


def place(settings: dict, cand, work, rec: dict, guard, *, say) -> dict:
    """Queue the existing downloader without copying, relabeling or trashing a source page."""
    from core import parser_manga as PM, parser_loose as L, manhwa_download as D, manhwa_sources as S
    from core import manhwa_queue as Q
    from core.redaction import sanitize_text
    if guard.stopped():
        return {"state": "", "more": True}
    source, key = S.source_for(str(rec.get("url") or ""))
    now = int(time.time())
    values = {"copied": False, "copy_error": "", "cards_to_trash": False, "copy_retry_at": now + POLL_AFTER}
    if source is None or source.name != "ehentai" or not key:
        values.update(fill={"state": "failed", "words": "Нет полной ссылки на галерею E-Hentai"}, copy_retry_at=0)
        return _put(rec, values, guard)
    url = f"{source.site()}/g/{key}/"
    linked = D.linked_folder(settings, source.name, key)
    if linked:
        kind, folder = linked
        values.update(had=str(folder), had_kind=kind, folder=Path(folder).name)
        if have(Path(folder), key):
            values.update(copied=True, copy_retry_at=0,
                          fill={"state": "done" if rec.get("eh_queued_at") else "have"})
            return _put(rec, values, guard)
    if not PM.fill_on(settings):
        values.update(fill={"state": "off"}, copy_retry_at=now + OFF_AFTER)
    elif _going(source, key, url):
        values["fill"] = {"state": "queued"}
    elif int(rec.get("eh_queue_tries") or 0) >= L.QUEUE_TRIES:
        values.update(fill={"state": "failed", "words": "Галерея не скачалась — повторите по ссылке"}, copy_retry_at=0)
    elif rec.get("eh_queued_at") and int(rec["eh_queued_at"]) + L.QUEUE_AGAIN > now:
        retry = int(rec["eh_queued_at"]) + L.QUEUE_AGAIN
        values.update(fill={"state": "again", "at": retry}, copy_retry_at=retry)
    elif L._person_busy():
        values.update(fill={"state": "held"}, copy_retry_at=now + OFF_AFTER)
    else:
        title = sanitize_text(str(rec.get("title") or work.name or "Галерея E-Hentai"))
        record = {"url": url, "title": title, "where": "manga", "branch": "", "follow": True,
                  "helpers": False, "chapters": None, "hentai": True}

        def download(say_, stop_):
            return Q.work(record, settings, say_, stop_)

        with PM.STORE._lock:
            if guard.stopped():
                return {"state": "", "more": True}
            if not _going(source, key, url):
                Q.start(f"Скачать: {title}", download, record=record,
                        about="eh-photo:" + key, title=title, count=1)
                values.update(eh_queued_at=now, eh_queue_tries=int(rec.get("eh_queue_tries") or 0) + 1)
            values["fill"] = {"state": "queued"}
            result = _put(rec, values, guard)
        if result.get("state"):
            say(PM.line("МАНГА", f"«{title}» — полная галерея скачивается в «Мангу» (видно в «Загрузках»)"))
        return result
    return _put(rec, values, guard)


def _put(rec: dict, values: dict, guard) -> dict:
    return {**rec, **values} if guard.put(values) else {"state": "", "more": True}
