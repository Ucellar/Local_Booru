"""Small JSON endpoints used by the pages: tag hints while typing, card actions."""
from __future__ import annotations

import logging
import re
import threading

from core.database.connection import db
from webui import data
from webui.server import Request, Response

_LOG = logging.getLogger("local_booru.webui")
TROUBLES = ("short", "error", "splice", "format", "gave_up")


def register(router) -> None:
    router.add("GET", r"/api/autocomplete", autocomplete)
    router.add("POST", r"/api/prefs", save_prefs)
    router.add("POST", r"/api/app/(?P<name>[a-z-]+)", app_request)
    router.add("POST", r"/api/post/(?P<id>\d+)/favorite", favorite)
    router.add("POST", r"/api/post/(?P<id>\d+)/rating", rating)
    router.add("POST", r"/api/post/(?P<id>\d+)/trash", trash)
    router.add("POST", r"/api/post/(?P<id>\d+)/restore", restore)
    router.add("POST", r"/api/post/(?P<id>\d+)/open", open_external)
    router.add("POST", r"/api/post/(?P<id>\d+)/reveal", reveal)
    router.add("POST", r"/api/post/(?P<id>\d+)/picture-trouble", picture_trouble)


def picture_trouble(req: Request) -> Response:
    """The post page says its picture came wrong — short, not opening, or unlike its own thumbnail in some rows
    (bytes of another file in it) — and asked for it again: one line in the program log, so the next look at
    «картинки показываются кусками» starts from what the window saw."""
    body = req.json()
    kind = str(body.get("kind") or "")
    if kind not in TROUBLES:
        return Response.json({"ok": False, "error": "Неизвестная беда"}, 400)
    detail = [f"{key}={_whole(body.get(key))}" for key in ("tries", "got", "bytes") if _whole(body.get(key)) is not None]
    rows = body.get("rows")
    if isinstance(rows, list):
        detail.append("строки " + " ".join(str(_whole(v)) for v in rows[:48] if _whole(v) is not None))
    _LOG.warning("картинка карточки %s пришла не так (%s): %s", int(req.params["id"]), kind, ", ".join(detail))
    return Response.json({"ok": True})


def _whole(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def autocomplete(req: Request) -> Response:
    term = req.arg("q").strip()
    negate = term.startswith("-")
    hits = data.autocomplete(req.settings, term.lstrip("-"))
    for hit in hits:
        hit["insert"] = ("-" if negate else "") + hit["tag"]
    return Response.json({"items": hits})


def save_prefs(req: Request) -> Response:
    from webui import prefs
    stored = prefs.save(req.json())
    return Response.json({"ok": True, "prefs": stored})


_HEX_COLOR = __import__("re").compile(r"#[0-9a-fA-F]{6}")


def _episode_file(req: Request, episode_id: str):
    """(path, "") of an episode of the tab «Видео», or (None, why not)."""
    from pathlib import Path

    from core import video_library
    root = video_library.video_root(req.settings)
    _series, episode = video_library.LIBRARY.episode(root, episode_id) if Path(root).is_dir() else (None, None)
    if episode is None:
        return None, "Такой серии нет — возможно, файл переименовали или убрали"
    if not Path(episode.path).is_file():
        return None, "Файла этой серии нет на диске"
    return Path(episode.path), ""


def _series_of(req: Request, episode_id: str) -> dict:
    """What a player needs of the series of an episode of the tab «Видео»: "series" — its id (the choice of tracks is
    kept for it), "prefer" — that choice, "top" — its folder (subtitles there are its episodes' too); {} — none."""
    from pathlib import Path

    from core import video_library
    root = video_library.video_root(req.settings)
    series, _episode = video_library.LIBRARY.episode(root, episode_id) if Path(root).is_dir() else (None, None)
    if series is None:
        return {}
    return {"series": series.id, "prefer": video_library.PROGRESS.tracks(series.id),
            "top": "" if series.single else series.path}


def _taking_over(body: dict, prefer: dict) -> dict:
    """Where the full-size player takes over from the page's (⛶): its moment (``start_at``), whether it was paused,
    the furthest place reached (``resume`` — «Продолжить просмотр»), the voice-over and subtitles chosen there
    (``prefer``: on top of the series' own choice).  Only numbers and short names come from the page."""
    def number(key: str) -> float:
        try:
            value = float(body.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(10.0 ** 7, value)) if value == value else 0.0     # NaN is no moment
    out = {"start_at": number("from"), "paused": body.get("playing") is False, "resume": number("resume")}
    tracks = body.get("tracks")
    if isinstance(tracks, dict):
        chosen = {k: dict(v) for k, v in (prefer or {}).items() if isinstance(v, dict)}
        for kind in ("audio", "subtitle"):
            value = tracks.get(kind)
            if not isinstance(value, dict):
                continue
            if kind == "subtitle" and value.get("off") is True:
                chosen[kind] = {"off": True}
                continue
            clean = {k: str(value.get(k) or "")[:120] for k in ("name", "title", "lang") if value.get(k)}
            if clean:
                if kind == "subtitle" and value.get("file") is True:
                    clean["file"] = True
                chosen[kind] = clean
        out["prefer"] = chosen
    return out


def _app_payload(req: Request, name: str):
    """What the program window needs for ``name``, looked up here (in the server thread);
    (payload, None) or (None, error response)."""
    body = req.json()
    if name == "play_video" and body.get("episode"):
        path, why = _episode_file(req, str(body.get("episode")))
        if path is None:
            return None, Response.json({"ok": False, "error": why}, 404)
        of = _series_of(req, str(body.get("episode")))
        payload = {"path": str(path), "id": None, "title": path.stem, "episode": str(body.get("episode")), **of}
        payload.update(_taking_over(body, of.get("prefer") or {}))
        return payload, None
    if name in {"play_video", "copy_file", "drag_file"}:
        try:
            image_id = int(body.get("id"))
        except (TypeError, ValueError):
            return None, Response.json({"ok": False, "error": "Нет номера карточки"}, 400)
        path, why = data.media_file_of(req.settings, image_id, video=name == "play_video")
        if path is None:
            return None, Response.json({"ok": False, "error": why}, 404)
        payload = {"path": str(path), "id": image_id}
        if name in {"play_video", "drag_file"}:
            item = data.post_detail(req.settings, image_id) or {}
            names = sorted({t for v in (item.get("tag_groups") or {}).values() for t in v})
            payload["title"] = " ".join(names[:12])
            if name == "play_video":                  # a card's video: from the page's moment too (round and round)
                taken = _taking_over(body, {})
                payload.update(start_at=taken["start_at"], paused=taken["paused"], resume=taken["resume"],
                               **({"prefer": taken["prefer"]} if taken.get("prefer") else {}))
            if name == "drag_file":
                payload["item"] = {"path": str(path), "tags": names, "tag_groups": item.get("tag_groups") or {},
                                   "sources": item.get("sources") or [], "hash_md5": item.get("hash_md5") or ""}
        return payload, None
    if name == "tag_color":
        from core.tag_utils import canonical_tag_key
        tag = canonical_tag_key(str(body.get("tag") or ""))
        color = str(body.get("color") or "")
        if not tag or (color and not _HEX_COLOR.fullmatch(color)):
            return None, Response.json({"ok": False, "error": "Нужен тег и цвет вида #rrggbb"}, 400)
        return {"tag": tag, "color": color.lower()}, None
    if name == "choose_path":
        from webui.settings_store import PATH_KEYS
        key = str(body.get("key") or "")
        if key not in PATH_KEYS:
            return None, Response.json({"ok": False, "error": "Эту настройку так не выбрать"}, 400)
        kind, title, flt = PATH_KEYS[key]
        return {"key": key, "kind": kind, "title": title, "filter": flt,
                "current": str(req.settings.get(key) or "")}, None
    if name == "export_results":
        ids = data.result_ids(req.settings, str(body.get("tags") or ""), section=str(body.get("section") or "all"),
                              source=str(body.get("source") or "all"))
        return {"ids": ids}, None
    if name == "video":
        return _video_payload(req, body)
    if name == "video_screen":
        return {"on": body.get("on") is True}, None
    return {}, None


VIDEO_COMMANDS = ("toggle", "play", "pause", "seek", "volume", "mute", "size", "stop", "audio", "subtitle")
_PLAYS: dict = {}                    # the token of an episode's play -> its series (its choice of tracks is kept)
_PLAYS_LOCK = threading.Lock()
_SUBTITLE = re.compile(r"-1|[0-9]{1,3}|x[0-9]{1,3}")
_AUDIO = re.compile(r"[0-9]{1,3}")


def _choice(body: dict, kind: str, value) -> dict | None:
    """What is kept of a voice-over or subtitles chosen on the page (its name, title and language as the page has
    them): the next episode of the series gets the same one."""
    if kind == "subtitle" and value == "-1":
        return {"off": True}
    out = {key: str(body.get(key) or "")[:120] for key in ("name", "title", "lang")}
    if kind == "subtitle" and str(value).startswith("x"):
        out["file"] = True
    return out if any(out.get(k) for k in ("name", "title", "lang")) else None


def _video_payload(req: Request, body: dict):
    """A video played inside the page (webui/video_relay.py): «start» looks the file up and makes the play's
    token (the page shows ``/video/live/<token>``); the other commands go to the play of their token."""
    import secrets
    from webui.video_relay import RELAY
    cmd = str(body.get("cmd") or "")

    def number(key, lo, hi, default=0.0):
        try:
            return max(lo, min(hi, float(body.get(key))))
        except (TypeError, ValueError):
            return default
    if cmd == "start":
        if body.get("episode"):                  # an episode of the tab «Видео»: by its id, never by a path
            path, why = _episode_file(req, str(body.get("episode")))
            image_id = 0
        else:
            try:
                image_id = int(body.get("id"))
            except (TypeError, ValueError):
                return None, Response.json({"ok": False, "error": "Нет номера карточки"}, 400)
            path, why = data.media_file_of(req.settings, image_id, video=True)
        if path is None:
            return None, Response.json({"ok": False, "error": why}, 404)
        token = secrets.token_urlsafe(12)
        RELAY.begin(token)                       # the page may ask for the frames at once
        payload = {"cmd": "start", "id": image_id, "path": str(path), "token": token,
                   "w": int(number("w", 16, 4096, 960)), "h": int(number("h", 16, 4096, 540)),
                   "from": number("from", 0, 10 ** 7), "loop": body.get("loop") is not False}
        if body.get("episode"):
            payload["episode"] = str(body.get("episode"))
            payload.pop("id")
            of = _series_of(req, payload["episode"])
            if of:
                payload.update(prefer=of["prefer"], top=of["top"])
                with _PLAYS_LOCK:
                    _PLAYS[token] = of["series"]
                    while len(_PLAYS) > 16:
                        _PLAYS.pop(next(iter(_PLAYS)))
        return payload, None
    if cmd not in VIDEO_COMMANDS:
        return None, Response.json({"ok": False, "error": "Неизвестная команда видео"}, 400)
    token = str(body.get("token") or "")
    if not RELAY.active(token):
        return None, Response.json({"ok": False, "error": "Это видео уже не играет", "over": True}, 409)
    payload = {"cmd": cmd, "token": token}
    if cmd == "seek":
        payload["value"] = number("value", 0, 10 ** 7)
    elif cmd == "volume":
        payload["value"] = int(number("value", 0, 100, 50))
    elif cmd == "mute":
        payload["value"] = bool(body.get("value"))
    elif cmd == "size":
        payload["w"], payload["h"] = int(number("w", 16, 4096, 960)), int(number("h", 16, 4096, 540))
    elif cmd in ("audio", "subtitle"):
        value = str(body.get("value") if body.get("value") is not None else "")
        if not (_SUBTITLE if cmd == "subtitle" else _AUDIO).fullmatch(value):
            return None, Response.json({"ok": False, "error": "Нет такой дорожки"}, 400)
        payload["value"] = value if cmd == "subtitle" else int(value)
        with _PLAYS_LOCK:
            series = _PLAYS.get(token)
        if series:                               # the choice is the series': its next episode opens with it
            from core import video_library
            video_library.PROGRESS.set_tracks(series, cmd, _choice(body, cmd, value))
    return payload, None


def app_request(req: Request) -> Response:
    """Things only the program window can do: dialogs, the clipboard, the video player, dragging out."""
    from webui import service
    name = req.params["name"].replace("-", "_")
    if name in ("save_settings", "import_profile"):   # only the server changes settings (after its own checks)
        return Response.json({"ok": False, "error": "Неизвестное действие"}, 404)
    bridge = service.bridge()
    if bridge is None:
        return Response.json({"ok": False, "error": "Это можно сделать только в окне программы Local Booru"}, 409)
    payload, error = _app_payload(req, name)
    if error is not None:
        return error
    if not bridge.request(name, payload):
        if name == "video" and payload.get("cmd") == "start":
            from webui.video_relay import RELAY
            RELAY.end(payload["token"])
        return Response.json({"ok": False, "error": "Неизвестное действие"}, 404)
    if name == "video" and payload.get("cmd") == "start":
        return Response.json({"ok": True, "token": payload["token"]})
    return Response.json({"ok": True})


def _row(req: Request):
    with db(req.settings, readonly=True) as con:
        row = con.execute("SELECT id, path, deleted, lifecycle FROM images WHERE id=?", (int(req.params["id"]),)).fetchone()
    return dict(row) if row else None


def favorite(req: Request) -> Response:
    row = _row(req)
    if row is None:
        return Response.json({"ok": False, "error": "Карточки нет"}, 404)
    value = 1 if int(req.json().get("value", 1) or 0) else 0
    with db(req.settings, write=True) as con:
        con.execute("UPDATE images SET favorite=? WHERE id=?", (value, row["id"]))
    return Response.json({"ok": True, "favorite": value})


def rating(req: Request) -> Response:
    row = _row(req)
    if row is None:
        return Response.json({"ok": False, "error": "Карточки нет"}, 404)
    try:
        value = max(0, min(5, int(req.json().get("value", 0))))
    except (TypeError, ValueError):
        return Response.json({"ok": False, "error": "Оценка — число от 0 до 5"}, 400)
    with db(req.settings, write=True) as con:
        con.execute("UPDATE images SET rating=? WHERE id=?", (value, row["id"]))
    return Response.json({"ok": True, "rating": value})


def trash(req: Request) -> Response:
    row = _row(req)
    if row is None:
        return Response.json({"ok": False, "error": "Карточки нет"}, 404)
    if int(row["deleted"] or 0):
        return Response.json({"ok": True, "already": True})
    from core.library_lifecycle import move_to_trash
    from webui.server import FILE_STREAMS
    with FILE_STREAMS.holding(row["path"]):        # on Windows a file being sent cannot be moved
        result = move_to_trash(req.settings, [row], reason="gallery_context_delete", make_backup=True)
    data.TAG_COUNTS.invalidate()
    if result.get("error") or not result.get("trashed_records"):
        return Response.json({"ok": False, "error": result.get("error") or "Не удалось переместить в корзину"}, 409)
    return Response.json({"ok": True})


def restore(req: Request) -> Response:
    row = _row(req)
    if row is None:
        return Response.json({"ok": False, "error": "Карточки нет"}, 404)
    from core.library_lifecycle import restore_from_trash
    from webui.server import FILE_STREAMS
    with FILE_STREAMS.holding(row["path"]):
        result = restore_from_trash(req.settings, [row["id"]])
    data.TAG_COUNTS.invalidate()
    if result.get("restored") or result.get("merged_existing"):
        return Response.json({"ok": True, **{k: result.get(k) for k in ("restored", "merged_existing")}})
    return Response.json({"ok": False, "error": "Не удалось вернуть: файла нет ни в корзине, ни на месте"}, 409)


def _file_or_error(req: Request):
    path = data.file_path_of(req.settings, int(req.params["id"]))
    if path is None:
        return None, Response.json({"ok": False, "error": "Файла этой карточки нет на диске"}, 404)
    return path, None


def open_external(req: Request) -> Response:
    path, why = data.media_file_of(req.settings, int(req.params["id"]))
    if path is None:
        return Response.json({"ok": False, "error": why}, 404)
    from webui import desktop
    try:
        desktop.open_file(path)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не удалось открыть: {exc}"}, 500)
    return Response.json({"ok": True})


def reveal(req: Request) -> Response:
    path, error = _file_or_error(req)
    if error:
        return error
    from webui import desktop
    try:
        desktop.reveal(path)
    except OSError as exc:
        return Response.json({"ok": False, "error": f"Не удалось открыть папку: {exc}"}, 500)
    return Response.json({"ok": True})
