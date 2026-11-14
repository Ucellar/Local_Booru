"""An explicit title link for a row of loose manga pictures, resolved by the existing folder-link path.

The request marks the row; the background opens the title through the ordinary source and gate. Network failures
are retried, and an old request never overwrites a newer user choice. The originals are never written here.
"""
from __future__ import annotations

import time
import uuid


def say_link(settings: dict, row_key: str, url: str) -> dict:
    from core import parser_loose as L, parser_manga_again as A
    url = " ".join(str(url or "").split())[:A.LINK_MAX]
    if not url:
        return {"ok": False, "error": "Вставьте ссылку на страницу манги"}
    if not A.link_known(url):
        return {"ok": False, "error": "Эту ссылку программа не знает: нужна страница тайтла на " + A.link_words()}
    token = uuid.uuid4().hex

    def put(data: dict) -> bool:
        key = L._key_of(data, row_key)
        group = data["groups"].get(key)
        if group is None:
            return False
        if not group.get("pending_link"):
            group["link_previous"] = {k: group.get(k) for k in ("state", "hand", "by")}
        group.update(pending_link={"url": url, "token": token, "tries": 0}, state="", again=True, later_at=0)
        group.pop("hand", None)
        return True

    if not L.STORE.change(put):
        return {"ok": False, "error": "Такой строки в списке нет — откройте страницу заново"}
    return {"ok": True, "words": A._started_words(settings).replace("Перепроверяю", "Открываю ссылку")
            .replace("перепроверю", "открою ссылку")}


def settle(settings: dict, asker, key: str, group: dict, tell) -> dict:
    from core import manga_detect as MD, manhwa_sources as S
    from core import parser_loose as L, parser_manga as PM, parser_manga_again as A, parser_manga_nhentai as N
    pending = dict(group.get("pending_link") or {})
    url = str(pending.get("url") or "")
    work = MD.Work(key=key, name=str(group.get("name") or ""), path="", parts=[])
    got = A._by_hand(asker, work, url)
    if asker.stopped():
        return {"state": "", "more": True, "wait": 0.0}
    tries = int(pending.get("tries") or 0) + 1
    later = str(got.get("later") or "")
    refused = str(got.get("refused") or "")
    verdict = got.get("verdict")
    values = {}
    if verdict is not None:
        number = N.number_of(verdict.url)
        source, title_key = S.source_for(verdict.url) if not number else (None, str(number))
        if number or source is not None:
            values = {"state": PM.MANGA, "hand": PM.MANGA, "how": "hand", "url": verdict.url,
                      "title": verdict.title, "site": N.SITE if number else source.name, "key": title_key,
                      "label": N.LABEL if number else source.label, "gallery": bool(number or source.name == "ehentai"),
                      "eh_photo": bool(verdict.eh_photo), "seen_hash": "",
                      "adult": bool(number or got.get("adult") or getattr(source, "adult_only", False)),
                      "by": "ваша ссылка → «" + verdict.title + "»; картинки отнесены к ней по вашему выбору",
                      "later_at": 0, "placed": False, "fill": {}, "open": "", "queued_at": 0, "queue_tries": 0}
        else:
            refused = "по этой ссылке тайтла нет"
    accepted = bool(values)
    if later and tries <= 3:
        values = {"state": PM.WAIT, "how": "down", "by": "сайт по вашей ссылке не ответил — попробую ещё раз",
                  "later_at": int(time.time()) + PM.LATER,
                  "pending_link": {**pending, "tries": tries}}
    elif not values:
        previous = group.get("link_previous") or {}
        values = {"state": previous.get("state") or PM.NOT, "hand": previous.get("hand") or "",
                  "by": "Ссылка не принята: " + (refused or "сайт не ответил — попробуйте позже"), "later_at": 0}

    def put(data: dict) -> bool:
        current = data["groups"].get(key)
        if current is None or (current.get("pending_link") or {}).get("token") != pending.get("token"):
            return False
        current.update(values)
        waiting = values.get("state") == PM.WAIT
        if not waiting:
            for old in ("pending_link", "link_previous", "again"):
                current.pop(old, None)
        for rec in data["files"].values():
            if rec.get("group") != key or waiting or not accepted:
                continue
            if values["state"] == PM.MANGA:
                rec.update(state=PM.MANGA, how="hand", why="", page="")
            else:
                rec.update(state=PM.NOT, why=values["by"])
        return True

    if not L.STORE.change(put):
        return {"state": "", "more": True, "wait": 0.0}
    tell(PM.line("МАНГА" if values["state"] == PM.MANGA else "ЖДЁТ" if values["state"] == PM.WAIT else "НЕ МАНГА",
                 str(values["by"])))
    return {"state": values["state"], "more": False, "wait": 0.0}
