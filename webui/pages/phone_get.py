"""(v5.44.70) «Скачать по ссылке» — the phone's page for a link to a title or a gallery (the owner, 5.11.2026: «с телефона
нельзя вставить ссылки для скачивания (видео манга и манхва)»).

``/m/get`` (:func:`page`) is one field.  What the link is, the program says (:func:`what` — ``/api/phone/get/what``):
a gallery of nhentai (its link or its number — ``manga_download.gallery_ids``) is the manga; a title of a site of
manhwa (``manhwa_sources.source_for``) is the manhwa; any other page — the videos.  Then the page
(``static/phone-get.js``) does what the computer's pages do, by their own addresses: «Найти» and «Скачать» of
``video_get``, ``manhwa_get`` and ``manga_get``, their states, «Пауза» and «Остановить» — nothing of theirs changes
(the video download is the other chat's: its answers are only read).  The phone's «Поделиться» brings a link here too
(the manifest's ``share_target`` — ``?title=&text=&url=``; Android's Chrome puts the link in ``text``): the link is
looked for in all three and searched at once.  In «SFW» the galleries of nhentai are not taken (the computer's page of
them is hidden then).
"""
from __future__ import annotations

import re

from webui.html import esc
from webui.pages import phone
from webui.server import Request, Response

_LINK = re.compile(r"https?://[^\s<>\"'«»]+", re.I)
_TAIL = ".,;:!?)]}»"                     # the end of a sentence after a link is not the link's
LINK_ICON = ('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/>'
             '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>')


def register(router) -> None:
    router.add("GET", r"/m/get", page)
    router.add("POST", r"/api/phone/get/what", what)


def first_link(text: str) -> str:
    """The first web address in ``text`` ("" — none)."""
    found = _LINK.search(str(text or ""))
    return found.group(0).rstrip(_TAIL) if found else ""


def asked_of(req: Request) -> tuple[str, bool]:
    """(what the field opens with, True — look for it at once).  ``?url=`` — the page's own memory of its field (no
    search: «назад» to the page must not search again); «Поделиться» of another app — ``?text=`` / ``?title=``: the
    first link in ``url``, ``text``, ``title`` (else the words themselves — a number of a gallery), searched at once;
    ``go=1`` — searched at once too."""
    shared = "text" in req.query or "title" in req.query
    for key in ("url", "text", "title"):
        link = first_link(req.arg(key))
        if link:
            return link, shared or req.arg("go") == "1"
    words = (req.arg("url") or req.arg("text") or req.arg("title")).strip()[:2000]
    return words, bool(words) and (shared or req.arg("go") == "1")


def what(req: Request) -> Response:
    """{text} → {kind: "manga", ids} | {kind: "manhwa", url, site} | {kind: "video", url} — or why it is none of them."""
    from core import manga_download, manhwa_sources
    from webui import content_mode
    text = str(req.json().get("text") or "").strip()[:4000]
    if not text:
        return Response.json({"ok": False, "error": "Вставьте ссылку"}, 400)
    link = first_link(text)
    numbers, bad = manga_download.gallery_ids(text)
    if (bad or not numbers) and link:                # a link of nhentai among words («Поделиться» adds its title)
        numbers, bad = manga_download.gallery_ids(link)
    if numbers and not bad:
        if content_mode.mode() == "sfw":
            return Response.json({"ok": False, "sfw": True, "error": "Включён режим «SFW» — галереи nhentai не "
                                                                    "скачиваются. «Показать всё» — и они скачиваются."},
                                 409)
        if len(numbers) > manga_download.MAX_AT_ONCE:
            return Response.json({"ok": False, "error": f"За один раз — не больше {manga_download.MAX_AT_ONCE} "
                                                        "галерей"}, 400)
        return Response.json({"ok": True, "kind": "manga", "ids": numbers})
    if not link:
        return Response.json({"ok": False, "error": "Это не ссылка — вставьте адрес страницы тайтла (он начинается с "
                                                    "https://) или номер галереи nhentai"}, 400)
    source, _key = manhwa_sources.source_for(link)
    if source is not None:
        from webui.pages.manhwa_get import adult_refusal
        refused = adult_refusal(source)                    # (v5.44.85) a site of hentai alone in «SFW»
        if refused:
            return Response.json({"ok": False, "sfw": True, "error": refused}, 409)
        return Response.json({"ok": True, "kind": "manhwa", "url": link, "site": source.label})
    return Response.json({"ok": True, "kind": "video", "url": link})


def _video_sites(settings: dict) -> str:
    try:
        from core import video_dubs
        dubs = getattr(video_dubs, "AVAILABLE", True) and video_dubs.enabled(settings)
    except (ImportError, AttributeError):
        dubs = False
    return "YummyAnime и другие сайты, где видео открыто" if dubs else "сайты, где видео открыто"


def page(req: Request) -> Response:
    from webui import content_mode
    asked, at_once = asked_of(req)
    sfw = content_mode.mode() == "sfw"
    kinds = [f"<li><b>Видео</b> — страница тайтла или серии ({esc(_video_sites(req.settings))})</li>",
             "<li><b>Манхва и манга</b> — тайтл на Remanga, ReComics, MangaLib, MangaBuff, ReadManga, Desu, "
             "com-x.life, MangaDex</li>"]
    if not sfw:
        kinds.append("<li><b>nhentai</b> — ссылка на галерею или её номер</li>")
        kinds.append("<li><b>Хентай-тян, HentaiLib, AllHentai</b> — ссылка на тайтл</li>")   # (v5.44.85, v5.44.99)
    merge = bool(req.settings.get("video_dub_merge", True))
    body = f"""
<section class="m-get" data-get data-merge="{'1' if merge else ''}"{' data-autofind="1"' if at_once else ''}>
  <form class="m-get-ask" data-get-form>
    <label class="m-field m-get-field">{phone.svg(LINK_ICON, size=20)}<span class="m-sr">Ссылка</span>
      <input name="url" type="text" inputmode="url" enterkeyhint="go" autocomplete="off" autocapitalize="off"
        spellcheck="false" placeholder="Вставьте ссылку" value="{esc(asked)}"></label>
    <div class="m-get-buttons">
      <button type="button" class="m-button m-quiet" data-get-paste hidden>Вставить</button>
      <button type="submit" class="m-button m-get-find">Найти</button>
    </div>
  </form>
  <ul class="m-get-kinds">{"".join(kinds)}</ul>
  <div class="m-get-step" data-get-step role="status" hidden></div>
  <section class="m-get-found" data-get-found aria-label="Что нашлось" hidden></section>
  <section class="m-get-going" data-get-going aria-labelledby="m-get-going-title" hidden>
    <h2 class="m-sub" id="m-get-going-title">Скачивание</h2>
    <div class="m-get-jobs" data-get-jobs></div>
  </section>
</section>"""
    return phone.page("Скачать по ссылке", body, heading="Скачать по ссылке", top=phone.mode_switch(),
                      scripts=("phone-get",))
