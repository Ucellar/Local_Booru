"""The viewer's choices for the web gallery (looks, thumbnail size), kept in one small file.

A file next to the program settings, not a browser cookie: the choice is the same in the program
window and in any browser, and it survives even if the embedded browser never wrote its cookies
to disk.  It is a separate file so it never races with the program saving ``app_settings.json``.

Every page of the menu keeps a look of its own (:data:`PAGE_LOOKS`, v5.22): a page without a choice — or with «как у
галереи» (:data:`FOLLOW`) — looks like the gallery; the manga and the tab «Видео» have looks of their own until one is
chosen (nhentai, «Хаб»).  ``{"looks_all": id}`` gives one look to every page.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

_LOG = logging.getLogger("local_booru.webui")
_LOCK = threading.Lock()
_CACHE: dict = {"stamp": None, "data": {}}
THUMB_SIZES = {"s", "m", "l"}
MANGA_STRIP_PLACES = ("bottom", "side", "off")     # the strip of page thumbnails in the manga reader
POST_FIT = ("fit", "original")                     # a card's picture: fitted to the window, or in its own size
FIT_NOTE = ("on", "off")                           # the box «Картинка уменьшена под окно» (the «r34» look)
VIDEO_AUTONEXT = ("on", "off")                     # the tab «Видео»: the next episode starts by itself
SETS_BOX = ("shown", "hidden")                     # the сборка over a card's picture: its previews or only its line

# the page of the menu (``page_frame``'s ``active``) -> (its key in the file, its look when none is chosen: None — the
# gallery's); the gallery's own key is "skin"
PAGE_LOOKS = {
    "posts": ("skin", "auto"),
    "tags": ("tags_skin", None),
    "manga": ("manga_skin", "nhentai"),
    "videos": ("video_skin", "hub"),
    "grab": ("grab_skin", None),
    "parser": ("parser_skin", None),
    "subs": ("subs_skin", None),
    "sites": ("sites_skin", None),
    "dupes": ("dupes_skin", None),
    "diag": ("diag_skin", None),
    "settings": ("settings_skin", None),
}
FOLLOW = "gallery"                                 # a page's choice «как у галереи»
LOOK_KEYS = tuple(key for key, _default in PAGE_LOOKS.values())


def look(page: str, stored: dict | None = None) -> dict:
    """The look of a page of the menu: {"skin" — what it shows, "key" — where its choice is kept, "follows" — it looks
    like the gallery, "gallery" — the gallery's look}."""
    from webui import skins
    stored = load() if stored is None else stored
    gallery = skins.valid(str(stored.get("skin") or skins.DEFAULT))
    key, default = PAGE_LOOKS.get(page, PAGE_LOOKS["posts"])
    if key == "skin":
        return {"skin": gallery, "key": key, "follows": False, "gallery": gallery}
    chosen = str(stored.get(key) or "")
    if chosen in skins.BY_ID:
        return {"skin": chosen, "key": key, "follows": False, "gallery": gallery}
    if chosen == FOLLOW or default is None:
        return {"skin": gallery, "key": key, "follows": True, "gallery": gallery}
    return {"skin": skins.valid(default), "key": key, "follows": False, "gallery": gallery}


def choices(stored: dict | None = None) -> dict:
    """Each page's choice as «Настройки» shows it: a look's id, or :data:`FOLLOW`."""
    stored = load() if stored is None else stored
    out = {}
    for page, (key, _default) in PAGE_LOOKS.items():
        now = look(page, stored)
        out[key] = FOLLOW if now["follows"] else now["skin"]
    return out


def _file() -> Path:
    from core.paths import SETTINGS_DIR
    return Path(SETTINGS_DIR) / "webui_prefs.json"


def load() -> dict:
    path = _file()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _LOCK:
        if _CACHE["stamp"] != stamp:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                _LOG.warning("web gallery preferences unreadable (%s), using defaults", exc)
                data = {}
            _CACHE["stamp"], _CACHE["data"] = stamp, data if isinstance(data, dict) else {}
        return dict(_CACHE["data"])


def save(update: dict) -> dict:
    """Merge the valid keys of ``update`` into the file; returns what is stored now."""
    from webui import skins
    clean = {}
    if str(update.get("looks_all") or "") in skins.BY_ID:     # one look for every page (a page named too keeps it)
        clean.update({key: FOLLOW for key in LOOK_KEYS if key != "skin"}, skin=str(update["looks_all"]))
    for key in LOOK_KEYS:                              # the look of each page of the menu
        value = str(update.get(key) or "")
        if key in update and (value in skins.BY_ID or (value == FOLLOW and key != "skin")):
            clean[key] = value
    if "thumbs" in update and str(update["thumbs"]) in THUMB_SIZES:
        clean["thumbs"] = str(update["thumbs"])
    if "manga_strip" in update and str(update["manga_strip"]) in MANGA_STRIP_PLACES:
        clean["manga_strip"] = str(update["manga_strip"])
    if "post_fit" in update and str(update["post_fit"]) in POST_FIT:
        clean["post_fit"] = str(update["post_fit"])
    if "fit_note" in update and str(update["fit_note"]) in FIT_NOTE:
        clean["fit_note"] = str(update["fit_note"])
    if "video_autonext" in update and str(update["video_autonext"]) in VIDEO_AUTONEXT:
        clean["video_autonext"] = str(update["video_autonext"])
    if "sets_box" in update and str(update["sets_box"]) in SETS_BOX:
        clean["sets_box"] = str(update["sets_box"])
    path = _file()
    with _LOCK:
        current = {}
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        current.update(clean)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        _CACHE["stamp"] = None
    return current
