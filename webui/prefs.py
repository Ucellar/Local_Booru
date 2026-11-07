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
import re
import logging
import threading
from pathlib import Path

_LOG = logging.getLogger("local_booru.webui")
_LOCK = threading.Lock()
_CACHE: dict = {"stamp": None, "data": {}}
THUMB_SIZES = {"s", "m", "l"}
MANGA_STRIP_PLACES = ("bottom", "side", "off")     # the strip of page thumbnails in the manga reader
MANGA_SETS = ("on", "off")                         # (v5.44) the parts of one work as one tile of «Вся манга»
POST_FIT = ("fit", "original")                     # a card's picture: fitted to the window, or in its own size
FIT_NOTE = ("on", "off")                           # the box «Картинка уменьшена под окно» (the «r34» look)
VIDEO_AUTONEXT = ("on", "off")                     # the tab «Видео»: the next episode starts by itself
SETS_BOX = ("shown", "hidden")                     # the сборка over a card's picture: its previews or only its line
CONTENT_MODES = ("sfw", "all", "nsfw")             # (v5.44.53) the switch in the header (webui/content_mode.py)
MOTION = ("on", "off")                             # (v5.44.55) «Плавные переходы» (static/motion.js)
GET_KINDS = ("gallery", "manga", "manhwa", "video", "archive")  # (v5.44.75) the part of «Скачать» opened last
MANHWA_WIDTHS = ("720", "900", "1200", "full")     # (v5.44.59) the width of the strip of «Манхва»

# the page of the menu (``page_frame``'s ``active``) -> (its key in the file, its look when none is chosen: None — the
# gallery's); the gallery's own key is "skin"
PAGE_LOOKS = {
    "posts": ("skin", "auto"),
    "tags": ("tags_skin", None),
    "manga": ("manga_skin", "nhentai"),
    "manhwa": ("manhwa_skin", "deep"),                 # (v5.44.59) the strips of «Манхва»
    "videos": ("video_skin", "hub"),
    "get": ("grab_skin", None),                         # (v5.44.75) «Скачать» — the grabber's look goes on
    "parser": ("parser_skin", None),
    "subs": ("subs_skin", None),
    "sites": ("sites_skin", None),
    "dupes": ("dupes_skin", None),
    "diag": ("diag_skin", None),
    "settings": ("settings_skin", None),
}
FOLLOW = "gallery"                                 # a page's choice «как у галереи»
LOOK_KEYS = tuple(key for key, _default in PAGE_LOOKS.values())
VARIANT_KEYS = tuple(key + "_variant" for key in LOOK_KEYS)   # (v5.44.45) each page's light/dark side, kept on its own


def look(page: str, stored: dict | None = None) -> dict:
    """The look of a page of the menu: {"skin" — what it shows, "key" — where its choice is kept, "follows" — it looks
    like the gallery, "gallery" — the gallery's look, (v5.44.45) "variant" — «light»/«dark» the page shows now,
    "variant_key" — where that choice is kept (each page keeps its own, so the gallery and «Видео» switch
    independently)}."""
    from webui import skins
    stored = load() if stored is None else stored
    gallery = skins.valid(str(stored.get("skin") or skins.DEFAULT))
    gallery_side = skins.variant_of(gallery, str(stored.get("skin_variant") or ""))
    key, default = PAGE_LOOKS.get(page, PAGE_LOOKS["posts"])
    vkey = key + "_variant"

    def pack(skin_id: str, follows: bool) -> dict:
        own = str(stored.get(vkey) or "")
        if follows and own not in skins.VARIANTS:     # (v5.44.46) «как у галереи» — its side too, until the page has one
            own = gallery_side
        return {"skin": skin_id, "key": key, "follows": follows, "gallery": gallery,
                "variant": skins.variant_of(skin_id, own), "variant_key": vkey, "gallery_variant": gallery_side}

    if key == "skin":
        return pack(gallery, False)
    chosen = str(stored.get(key) or "")
    if chosen in skins.BY_ID:
        return pack(chosen, False)
    if chosen == FOLLOW or default is None:
        return pack(gallery, True)
    return pack(skins.valid(default), False)


def motion(stored: dict | None = None) -> str:
    """(v5.44.55) «Плавные переходы»: "on" (until turned off) or "off"."""
    stored = load() if stored is None else stored
    value = str(stored.get("motion") or "on")
    return value if value in MOTION else "on"


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
    """Merge the valid keys of ``update`` into the file; returns what is stored now.

    (v5.44.46) A page's side (``<key>_variant``) given as "" is forgotten: the page shows its look's own side again
    or, «как у галереи», the gallery's.  «На всех страницах» (``looks_all``) forgets the other pages' sides for the same
    reason (``skin_variant`` in the same update — the side every page gets)."""
    from webui import skins
    clean = {}
    drop: set = set()
    if str(update.get("looks_all") or "") in skins.BY_ID:     # one look for every page (a page named too keeps it)
        clean.update({key: FOLLOW for key in LOOK_KEYS if key != "skin"}, skin=str(update["looks_all"]))
        drop.update(vkey for vkey in VARIANT_KEYS if vkey != "skin_variant")
    for key in LOOK_KEYS:                              # the look of each page of the menu
        value = str(update.get(key) or "")
        if key in update and (value in skins.BY_ID or (value == FOLLOW and key != "skin")):
            clean[key] = value
    for vkey in VARIANT_KEYS:                          # (v5.44.45) its light/dark side
        if vkey not in update:
            continue
        value = str(update.get(vkey) or "")
        if value in skins.VARIANTS:
            clean[vkey] = value
            drop.discard(vkey)
        elif not value:
            drop.add(vkey)
    if "thumbs" in update and str(update["thumbs"]) in THUMB_SIZES:
        clean["thumbs"] = str(update["thumbs"])
    if "manga_strip" in update and str(update["manga_strip"]) in MANGA_STRIP_PLACES:
        clean["manga_strip"] = str(update["manga_strip"])
    if "manga_sets" in update and str(update["manga_sets"]) in MANGA_SETS:
        clean["manga_sets"] = str(update["manga_sets"])
    if "post_fit" in update and str(update["post_fit"]) in POST_FIT:
        clean["post_fit"] = str(update["post_fit"])
    if "fit_note" in update and str(update["fit_note"]) in FIT_NOTE:
        clean["fit_note"] = str(update["fit_note"])
    if "video_autonext" in update and str(update["video_autonext"]) in VIDEO_AUTONEXT:
        clean["video_autonext"] = str(update["video_autonext"])
    if "sets_box" in update and str(update["sets_box"]) in SETS_BOX:
        clean["sets_box"] = str(update["sets_box"])
    if "content_mode" in update and str(update["content_mode"]) in CONTENT_MODES:
        clean["content_mode"] = str(update["content_mode"])
    if "motion" in update and str(update["motion"]) in MOTION:
        clean["motion"] = str(update["motion"])
    if "manhwa_width" in update and str(update["manhwa_width"]) in MANHWA_WIDTHS:
        clean["manhwa_width"] = str(update["manhwa_width"])
    if "get_kind" in update and str(update["get_kind"]) in GET_KINDS:
        clean["get_kind"] = str(update["get_kind"])
    # (v5.44.83) «Манхва → Лента»: the site and the order seen last (``core/manhwa_feed``)
    if "manhwa_feed_site" in update and re.fullmatch(r"[a-z]{2,20}", str(update["manhwa_feed_site"])):
        clean["manhwa_feed_site"] = str(update["manhwa_feed_site"])
    if "manhwa_feed_how" in update and str(update["manhwa_feed_how"]) in ("new", "popular"):
        clean["manhwa_feed_how"] = str(update["manhwa_feed_how"])
    # (v5.44.90) «Лента» of «Манга», «Манхва», «Видео»: what was chosen there last (the address's words)
    for key in ("feed_manga", "feed_manhwa", "feed_video"):
        if key in update and len(str(update[key])) <= 4000 and not re.search(r"[\s<>\"']", str(update[key])):
            clean[key] = str(update[key])
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
        for key in drop:
            current.pop(key, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        _CACHE["stamp"] = None
    return current
