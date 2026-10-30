"""«Брак → Получить теги по ссылке»: the person found the picture's post on a booru site (Google Lens, a search of
their own) and gives the address of that post; its tags make the Брак card a found one.

``check`` says at once, without the network, what is wrong with the request: not a web address, not the page of
one post, a site the program cannot ask, a card that is not in Брак, a card without its file.  ``apply`` asks the
site for the post — any site the program knows, ticked on «Сайты» or not: the person chose this post, nothing
filters it — and stores it the parser's own way (``core.parser.persist.save_found``): the file goes into the found
part of the library (or joins the live copy of the same bytes), its Брак copy and Брак entries go, the tags are
filtered as the parser filters them («Игнорировать числовые теги»).  The running program's settings are not
changed (the old page switched off «пропускать уже обработанные» for the whole program).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BRAK_BUCKETS = ("no_match", "downloaded_no_match")
EXAMPLE = "https://danbooru.donmai.us/posts/123 или https://rule34.xxx/index.php?page=post&s=view&id=123"


class ManualUrlError(Exception):
    """What is wrong, said to the person as it is."""


@dataclass
class ManualRequest:
    url: str                 # the address as given (trimmed)
    host: str                # the site's host
    post_id: str
    file: Path               # the card's file: its Брак copy (or the input file when the copy is gone)
    original: str            # where the parser found the file ("" when not known)
    image_id: int = 0        # the Брак card (0 when the file has none: the old window works by file)


def _offline_context(settings: dict):
    """A parser context that never reaches the network: the site is only looked up."""
    from core.parser.context import ParserContext
    from core.parser.http import HttpClient
    return ParserContext(settings, HttpClient(settings, cancel_check=lambda: True), lambda _m: None)


def _parse_address(settings: dict, url: str) -> tuple[str, str, str]:
    """(trimmed address, the site's host, post id) — or ManualUrlError."""
    from core.external_browser import is_web_url
    from core.parser.sites import HOST_ALIASES, table_config, site_for_host
    from core.parser.util import host_of, post_id_from_url
    url = str(url or "").strip()
    if not url:
        raise ManualUrlError(f"Вставьте ссылку на страницу поста, например {EXAMPLE}")
    if not is_web_url(url):
        raise ManualUrlError("Нужна веб-ссылка на страницу поста — она начинается с https:// или http://")
    host = host_of(url)
    post_id = post_id_from_url(url)
    if not host or not post_id:
        raise ManualUrlError(f"Это не страница одного поста. Нужна ссылка вида {EXAMPLE}")
    site = site_for_host(_offline_context(dict(settings)), host, detect=False)
    if site is None:
        name = HOST_ALIASES.get(host, host)
        if table_config(settings, name) is None:
            raise ManualUrlError(f"С сайта {host} программа теги брать не умеет: только с booru-сайтов (Danbooru, "
                                 "Gelbooru, rule34, e621, Moebooru…). Свой booru-сайт можно добавить на странице «Сайты».")
        return url, name, post_id            # a site of the table whose engine is found out when it is asked
    return url, site.host, post_id


def _card_file(settings: dict, path: str) -> tuple[Path, str]:
    """(the file to promote, the input path) of a Брак card's file — or ManualUrlError."""
    from core.nomatch_db import entry_for_path
    entry = entry_for_path(settings, path) or {}
    candidates = [path, entry.get("media_path"), entry.get("original_path")]
    file = next((Path(str(p)) for p in candidates if p and Path(str(p)).is_file()), None)
    if file is None:
        raise ManualUrlError("Файла этой карточки нет на диске — ни в библиотеке, ни на прежнем месте")
    return file, str(entry.get("original_path") or "")


def check(settings: dict, image_id: int, url: str) -> ManualRequest:
    """The request for a Брак card of the gallery, checked without the network."""
    from core.database.connection import db
    with db(settings, readonly=True) as con:
        row = con.execute("SELECT id, path, bucket, deleted FROM images WHERE id=?", (int(image_id),)).fetchone()
    if row is None:
        raise ManualUrlError("Такой карточки нет — возможно, её удалили")
    if int(row["deleted"] or 0) or str(row["bucket"] or "") not in BRAK_BUCKETS:
        raise ManualUrlError("Эта карточка не в Браке: её уже нашли или убрали в корзину")
    address, host, post_id = _parse_address(settings, url)
    file, original = _card_file(settings, str(row["path"] or ""))
    return ManualRequest(url=address, host=host, post_id=post_id, file=file, original=original,
                         image_id=int(row["id"]))


def check_file(settings: dict, path, url: str) -> ManualRequest:
    """The request for a file of the old window's page «Брак» (it lists files, not cards)."""
    address, host, post_id = _parse_address(settings, url)
    file, original = _card_file(settings, str(Path(path)))
    return ManualRequest(url=address, host=host, post_id=post_id, file=file, original=original)


def _still_brak(settings: dict, request: ManualRequest) -> bool:
    """Nothing promoted the file meanwhile (a parser run, the other window)."""
    from core.database.connection import db
    from core.nomatch_db import entry_for_path
    if request.image_id:
        with db(settings, readonly=True) as con:
            row = con.execute("SELECT bucket, deleted FROM images WHERE id=?", (request.image_id,)).fetchone()
        return row is not None and not int(row["deleted"] or 0) and str(row["bucket"] or "") in BRAK_BUCKETS
    return entry_for_path(settings, request.file) is not None or bool(
        request.original and entry_for_path(settings, request.original))


def library_busy() -> str:
    """Why nothing may be written to the library now ("" when it may)."""
    from core.database.connection import writes_blocked, writes_blocked_reason
    from core.parser import jobs as parser_jobs
    if writes_blocked():
        return f"База открыта только для чтения ({writes_blocked_reason()}) — сейчас ничего не записать"
    owner = parser_jobs.owner()
    if owner.startswith(parser_jobs.MAINTENANCE_OWNER.split("«")[0]):
        return parser_jobs.busy_text(owner) + " — дождитесь её конца"
    return ""


def apply(settings: dict, request: ManualRequest, *, progress: Callable[[str], None] | None = None,
          stop_check: Callable[[], bool] | None = None) -> dict:
    """Ask the site for the post and make the card a found one.  Returns {"ok": True, "image_id": the found card,
    "tags": how many, "url": the post's page, "action": what the library did} or {"stopped": True}; anything
    that stops it is ManualUrlError (said as it is)."""
    from core.parser.context import ParserContext
    from core.parser.http import Cancelled, HttpClient, HttpFailure
    from core.parser.models import Match
    from core.parser.sites import SiteUnavailable, site_for_host
    from core.redaction import sanitize_text
    say = progress or (lambda _m: None)
    stop = stop_check or (lambda: False)
    busy = library_busy()
    if busy:
        raise ManualUrlError(busy)
    work = {k: v for k, v in dict(settings).items() if not str(k).startswith("_")}
    http = HttpClient(work, log=say, cancel_check=stop)
    try:
        ctx = ParserContext(work, http, say)
        try:
            site = site_for_host(ctx, request.host)
        except Cancelled:
            return {"stopped": True}
        if site is None:
            raise ManualUrlError(f"Сайт {request.host} не ответил как booru-сайт — его движок не определить. "
                                 "Укажите тип сайта на странице «Сайты».")
        site.preflight()
        if site.disabled_reason:          # rule34.xxx without its key: the reason names the site
            raise ManualUrlError(f"{site.disabled_reason} — впишите это на странице «Сайты»")
        say(f"Спрашиваю {site.label} о посте {request.post_id}…")
        try:
            post = site.fetch_post(request.post_id)
        except Cancelled:
            return {"stopped": True}
        except (SiteUnavailable, HttpFailure) as exc:
            raise ManualUrlError(f"{site.label} не ответил: {sanitize_text(str(exc))}. Попробуйте ещё раз позже.") from exc
    finally:
        http.close_all()
    if post is None:
        raise ManualUrlError(f"На {site.label} нет поста {request.post_id} — или его видно только после входа на сайт "
                             "(логин и ключ — на странице «Сайты»)")
    if not post.tags:
        raise ManualUrlError(f"У поста {request.post_id} на {site.label} нет тегов")
    say(f"Пост найден: {post.url} — тегов: {len(post.tags)}")
    if stop():
        return {"stopped": True}
    busy = library_busy()
    if busy:
        raise ManualUrlError(busy)
    if not _still_brak(settings, request):
        raise ManualUrlError("Карточку тем временем уже нашли или убрали из Брака — ничего не изменено")
    if not request.file.is_file():
        raise ManualUrlError("Файл карточки пропал с диска — ничего не изменено")
    from core.file_hash_cache import get_or_compute_md5
    from core.parser.persist import save_found
    try:
        md5 = str(get_or_compute_md5(settings, request.file)[0] or "").lower()
    except OSError as exc:              # taken away meanwhile (the card was found another way), or unreadable
        if not _still_brak(settings, request):
            raise ManualUrlError("Карточку тем временем уже нашли или убрали из Брака — ничего не изменено") from exc
        raise ManualUrlError(f"Файл карточки не читается: {sanitize_text(str(exc))}") from exc
    if post.md5 and md5 and post.md5 != md5:
        say("У файла не тот MD5, что у оригинала на сайте: это другая копия той же картинки (уменьшенная или "
            "пересжатая). Теги поста записываются к ней.")
    saved = save_found(settings, request.file, [Match(post, method="manual_url", verified="manual")],
                       md5=md5, original_path=request.original or str(request.file),
                       still=lambda: _still_brak(settings, request))      # again, under the parser's write lock
    if saved.get("gone"):
        raise ManualUrlError("Карточку тем временем уже нашли или убрали из Брака — ничего не изменено")
    if not saved.get("ok"):
        raise ManualUrlError(f"Не сохранено: {saved.get('reason') or 'неизвестная ошибка'}")
    result = saved.get("result") or {}
    action = str(result.get("action") or "")
    if action == "skip_trashed":
        raise ManualUrlError(f"Эта же картинка лежит в корзине (карточка №{int(result.get('trashed_image_id') or 0)}) — "
                             "теги к ней не добавлены. Если она нужна, верните её из корзины.")
    if action == "skip_deleted":
        raise ManualUrlError("Эту картинку раньше удалили навсегда, и настройка «Если снова найден удалённый файл» не "
                             "даёт её вернуть: копия ушла в корзину.")
    if not int(result.get("image_id") or 0):
        raise ManualUrlError(f"Не сохранено: библиотека ответила «{action or 'нет ответа'}»")
    image_id = int(result["image_id"])
    from core.nomatch_db import set_manual_url
    for key in {request.original, str(request.file)} - {""}:
        set_manual_url(key, request.url, settings=settings)      # the closed Брак entry keeps where its tags came from
    say(f"Готово: карточка №{image_id} — найденная, тегов: {int(saved.get('tags') or 0)}")
    return {"ok": True, "image_id": image_id, "tags": int(saved.get("tags") or 0), "url": post.url,
            "action": action, "stopped": False}
