"""What is hentai — for the switch «SFW / Всё / NSFW» in the header of every page (v5.44.53, ``webui/content_mode.py``).

The owner's rules (4.11.2026):

* the switch is there to see less of it, not to lock it away;
* only real hentai is hidden — ecchi and fan service stay;
* a series is hentai by its genre «Хентай» (or Shikimori's «Rx») or by the site it came from — never by its age mark
  alone («Берсерк» is 18+);
* manga from nhentai and other hentai sites is hentai; other manga — when its site says so: Remanga's ``is_erotic``,
  MangaDex's «pornographic», the genre «Хентай» or «Эротика» (on the manga sites «Эротика» is the explicit one; ecchi
  is «Этти»);
* a mark by hand wins: «Это хентай» / «Это не хентай» on the title's page;
* (v5.44.59) a title of «Манхва» — as a manga: its site's mark (Remanga's ``is_erotic``), the genre or tag «Хентай» /
  «Эротика», a site of hentai.

Marks by hand are kept in ``content_marks.json`` in the settings folder — {"manga": {id: "hentai" | "safe"},
"series": {…}}; a mark that says what the program says anyway is not kept, so «Это хентай» on a title the program
took for one is «как решит программа» again.  The ids are the libraries' own (a path inside the folder): a renamed
folder loses its mark and is judged anew.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_LOG = logging.getLogger("local_booru.content")

KINDS = ("manga", "series", "manhwa")
VALUES = ("hentai", "safe")

# Sites of hentai (manga, video), of porn and of adult manhwa: the site's name is enough.
ADULT_HOST = re.compile(
    r"hentai|hentasis|hanime|henchan|h-chan\.me|allhen\.online|nude-?moon|hitomi\.la|pururin|tsumino|luscious|doujins\.com|"
    r"doujin\.sexy|multporn|8muses|myreadingmanga|porn|xvideos|xnxx|xhamster|spankbang|rule34|e621|"
    r"(?:manhwa|manga|toon|comic)s?-?18|18-?(?:manhwa|manga|comic)|hiperdex|manytoon", re.I)
# A manga's tag or genre that says it is hentai (as ``manga_library.norm_tag`` writes them).
MANGA_WORDS = {"хентай", "hentai", "эротика", "порно", "porn", "pornographic"}
# A series' genre (or tag, or type) that says it is hentai: only «Хентай» — «Эротика» of the anime sites is R+.
SERIES_WORDS = {"хентай", "hentai"}
_RX = re.compile(r"(?<![a-z])rx(?![a-z])", re.I)          # Shikimori's «Rx — хентай»


def adult_site(address: str) -> str:
    """The site's name when it is a site of hentai or porn, else ""."""
    text = str(address or "").strip()
    if not text:
        return ""
    host = urlparse(text if "//" in text else "//" + text).hostname or ""
    host = host.removeprefix("www.")
    return host if host and ADULT_HOST.search(host) else ""


def _norm(value) -> str:
    return str(value or "").strip().lower().replace("ё", "е").replace(" ", "_")


def _words(value) -> list:
    if isinstance(value, list):
        return [_norm(v) for v in value if str(v or "").strip()]
    if isinstance(value, str):
        return [_norm(v) for v in re.split(r"[,;/\n]+", value) if v.strip()]
    return []


# ── what the program says by itself ───────────────────────────────────────────────────────────────────────────
def manga_reason(manga) -> str:
    """Why the program takes this manga (a title or a series — not a chapter) for hentai, as its page says it;
    "" — it does not."""
    for part in (manga, *(getattr(manga, "chapters", None) or [])):     # a series: its chapters' sites too
        site = adult_site(getattr(part, "source_url", ""))
        if site:
            return f"с {site}"
        flag = str(getattr(part, "adult", "") or "")
        if flag:
            return flag
    groups = getattr(manga, "groups", {}) or {}                        # a series has its chapters' tags
    for group in ("general", "category"):
        for tag in groups.get(group, []) or []:
            if _norm(tag) in MANGA_WORDS:
                return f"тег «{str(tag).replace('_', ' ')}»"
    return ""


def manhwa_reason(title) -> str:
    """(v5.44.59) Why the program takes a title of «Манхва» for hentai (``core/manhwa_library.py``); "" — it does not."""
    info = getattr(title, "info", {}) or {}
    if info.get("hentai") is True:
        return "помечена как хентай в описании"
    if info.get("is_erotic") is True:
        return "сайт пометил как эротику"
    if str(info.get("content_rating") or "").strip().lower() == "pornographic":
        return "сайт пометил как порнографию"
    for key, what in (("genres", "жанр"), ("tags", "тег")):
        for word in _words(info.get(key)):
            if word in MANGA_WORDS:
                return f"{what} «{word.replace('_', ' ')}»"
    places = [info.get("source_url"), info.get("site")]
    places += [s.get("url") for s in info.get("sources") or [] if isinstance(s, dict)]
    for place in places:
        site = adult_site(str(place or ""))
        if site:
            return f"с {site}"
    return ""


def series_reason(series) -> str:
    """Why the program takes this series (or film) of «Видео» for hentai; "" — it does not."""
    info = getattr(series, "info", {}) or {}
    for key, what in (("genres", "жанр"), ("tags", "тег"), ("type", "тип")):
        for word in _words(info.get(key)):
            if word in SERIES_WORDS:
                return f"{what} «Хентай»"
    if _RX.search(str(info.get("age") or "")):
        return "возраст Rx — хентай"
    for key in ("site", "source_url"):
        site = adult_site(str(info.get(key) or ""))
        if site:
            return f"с {site}"
    return ""


# ── marks by hand ────────────────────────────────────────────────────────────────────────────────────────────
_LOCK = threading.Lock()
_CACHE: dict = {"stamp": None, "data": {}}


def _file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / "content_marks.json"


def marks() -> dict:
    """{"manga": {id: "hentai" | "safe"}, "series": {…}} — what was marked by hand."""
    path = _file()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {kind: {} for kind in KINDS}
    with _LOCK:
        if _CACHE["stamp"] != stamp:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                _LOG.warning("content marks unreadable (%s): no marks by hand", exc)
                data = {}
            clean = {}
            for kind in KINDS:
                part = data.get(kind) if isinstance(data, dict) else None
                clean[kind] = ({str(k): v for k, v in part.items() if v in VALUES}
                               if isinstance(part, dict) else {})
            _CACHE["stamp"], _CACHE["data"] = stamp, clean
        return {kind: dict(part) for kind, part in _CACHE["data"].items()}


def set_mark(kind: str, item_id: str, value: str) -> None:
    """Keep a mark by hand ("hentai" / "safe"), or forget it ("")."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if value and value not in VALUES:
        raise ValueError(f"unknown mark {value!r}")
    path = _file()
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        part = data.get(kind) if isinstance(data.get(kind), dict) else {}
        if value:
            part[str(item_id)] = value
        else:
            part.pop(str(item_id), None)
        data[kind] = part
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(path)
        _CACHE["stamp"] = None


# ── the verdict ──────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Verdict:
    hentai: bool
    reason: str          # as the title's page says it: «с nhentai.net», «жанр «Хентай»», «отмечено вручную»
    by_hand: bool        # a mark by hand decided it
    auto: bool           # what the program says by itself


def verdict(kind: str, item_id: str, auto_reason: str, hand: dict | None = None) -> Verdict:
    """The program's word (``auto_reason``) and the mark by hand together; ``hand`` — ``marks()[kind]`` when the
    caller judges many titles at once."""
    hand = marks().get(kind, {}) if hand is None else hand
    auto = bool(auto_reason)
    value = hand.get(str(item_id), "")
    if value == "hentai":
        return Verdict(True, "отмечено вручную", True, auto)
    if value == "safe":
        return Verdict(False, "отмечено вручную", True, auto)
    return Verdict(auto, auto_reason, False, auto)


def manga_verdict(manga, hand: dict | None = None) -> Verdict:
    return verdict("manga", manga.id, manga_reason(manga), hand)


def series_verdict(series, hand: dict | None = None) -> Verdict:
    return verdict("series", series.id, series_reason(series), hand)


def manhwa_verdict(title, hand: dict | None = None) -> Verdict:
    return verdict("manhwa", title.id, manhwa_reason(title), hand)


REASONS = {"manga": manga_reason, "series": series_reason, "manhwa": manhwa_reason}


def mark(kind: str, item, hentai: bool) -> Verdict:
    """«Это хентай» / «Это не хентай» on a title's page; the same as the program's own word — the mark is forgotten."""
    reason = REASONS[kind](item)
    set_mark(kind, item.id, "" if bool(hentai) == bool(reason) else ("hentai" if hentai else "safe"))
    return verdict(kind, item.id, reason)
