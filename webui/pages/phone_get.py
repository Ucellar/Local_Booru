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
them is hidden then).  (v5.44.119) A title of a site of manga opens its one page (``/m/title?url=`` →
``webui/pages/title.py`` in the phone's frame) — its card here is gone; ``?url=…&go=1`` with a title goes there too.

(v5.44.122, step 5 of the plan — the mockup E, «норм мне нравится») The field understands what the computer's
«Скачать → По ссылке» does — one parse for both, ``get_link.classify`` (:func:`what` keeps the phone's names of the
kinds): a name — the sites of manga searched at once (``manhwa_search``: the titles as covers in their groups, one
picked — its page ``/m/title``); a post of a booru site — its card here (the grabber's machinery, as the computer's
«Галерея (booru)» opens such a link: ``/api/grab/search`` → the post, its tags, «В галерею» — ``/api/grab/download``,
a download of one post).  Until then a name was «Это не ссылка» and a post was taken for a page of a video.
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
# (v5.44.122) the phone's names of the kinds of ``get_link.classify`` (its answers have kept them: phone-get.js, checks)
PHONE_KINDS = {"nhentai": "manga", "site": "manhwa"}


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
    ``go=1`` — searched at once too; (v5.44.122) ``q`` — a name searched at once (the computer's ``/get?q=``)."""
    shared = "text" in req.query or "title" in req.query
    for key in ("url", "text", "title"):
        link = first_link(req.arg(key))
        if link:
            return link, shared or req.arg("go") == "1"
    words = (req.arg("url") or req.arg("text") or req.arg("title")).strip()[:2000]
    if not words and req.arg("q").strip():
        return req.arg("q").strip()[:2000], True
    return words, bool(words) and (shared or req.arg("go") == "1")


def what(req: Request) -> Response:
    """{text} → what it is, as the computer's «Скачать → По ссылке» says it — (v5.44.122) the same function,
    ``get_link.classify``: {kind: "manga", ids} (nhentai) | {kind: "manhwa", url, site} (a title of a site of manga) |
    {kind: "booru", url} (a post of a booru site) | {kind: "video", url} | {kind: "name", q} (words: a name to look
    for), each with ``label`` (the mark under the field) — or why it is none of them (``sfw`` — refused by the mode)."""
    from webui.pages import get_link
    answer = get_link.classify(str(req.json().get("text") or ""), req.settings)
    if "error" in answer:
        return Response.json({"ok": False, **answer}, 409 if answer.get("sfw") else 400)
    return Response.json({"ok": True, **answer, "kind": PHONE_KINDS.get(answer["kind"], answer["kind"])})


def _video_sites(settings: dict) -> str:
    try:
        from core import video_dubs
        dubs = getattr(video_dubs, "AVAILABLE", True) and video_dubs.enabled(settings)
    except (ImportError, AttributeError):
        dubs = False
    return "YummyAnime и другие сайты, где видео открыто" if dubs else "сайты, где видео открыто"


def page(req: Request) -> Response:
    from webui import content_mode
    from webui.pages import manhwa_get, title as T
    asked, at_once = asked_of(req)
    if at_once and T.is_title_link(asked):     # (v5.44.119) a title of a site: its one page (the phone's)
        return Response.redirect(T.address_for(asked, req.settings, phone=True, branch=req.arg("b").strip()))
    sfw = content_mode.mode() == "sfw"
    # (v5.44.116) the sites' names — the server's lists (``manhwa_get.site_names``), the ones of hentai not in «SFW»
    sites, adult_sites = manhwa_get.hint_sites(not sfw)
    # (v5.44.122, the mockup E) what the field understands — as the computer's: what is pasted and the mark it gets
    # under the field; a name too, and (not in «SFW») a post of a booru site, nhentai; under the results
    rows = [("название", "Название — поиск по сайтам"), ("тайтл манги, манхвы", "Манга и манхва · Remanga")]
    if not sfw:
        rows += [("пост booru", "Галерея (booru) · Danbooru"), ("nhentai: ссылка, номер", "Хентай · nhentai")]
    rows.append(("сериал или серия", "Видео"))
    more = [f"Манга и манхва — тайтл на {esc(sites)}; по названию ищется сразу на всех, кроме "
            f"{esc(' и '.join(manhwa_get.LINK_ONLY))}.",
            f"Видео — страница тайтла или серии ({esc(_video_sites(req.settings))})."]
    if not sfw:
        more.append(f"Хентай — тайтл на {esc(', '.join(manhwa_get.site_names(adult=True)))}; галерея nhentai — "
                    "ссылка или номер; пост booru-сайта — его карточка, «В галерею» скачает его.")
    kinds = (f'<section class="m-get-kinds" aria-labelledby="m-get-kinds-h"><h2 class="m-get-kinds-h" '
             f'id="m-get-kinds-h">Поле понимает — как на компьютере</h2><ul>'
             + "".join(f'<li><span>{esc(what)}</span><b class="m-get-chip">{esc(mark)}</b></li>' for what, mark in rows)
             + "</ul>" + "".join(f'<p class="m-get-kinds-more">{line}</p>' for line in more) + "</section>")
    merge = bool(req.settings.get("video_dub_merge", True))
    # (v5.44.120) «Скачать» of the phone — «По ссылке» (this page) and «Загрузки» (``downloads.phone_page``: the
    # computer's board); until then the downloads going were a section at the foot of this page
    from webui.pages import downloads
    body = downloads.phone_chips("link") + f"""
<section class="m-get" data-get data-merge="{'1' if merge else ''}"{' data-autofind="1"' if at_once else ''}
  data-sites="{esc(sites)}" data-adult-sites="{esc(adult_sites)}">
  <form class="m-get-ask" data-get-form>
    <label class="m-field m-get-field">{phone.svg(LINK_ICON, size=20)}<span class="m-sr">Ссылка или название</span>
      <input name="url" type="text" enterkeyhint="search" autocomplete="off" autocapitalize="off"
        spellcheck="false" placeholder="Ссылка или название" value="{esc(asked)}"></label>
    <p class="m-get-mark" data-get-mark role="status" hidden></p>
    <div class="m-get-buttons">
      <button type="button" class="m-button m-quiet" data-get-paste hidden>Вставить</button>
      <button type="submit" class="m-button m-get-find">Найти</button>
    </div>
  </form>
  <div class="m-get-step" data-get-step role="status" hidden></div>
  <section class="m-get-found" data-get-found aria-label="Что нашлось" hidden></section>
  {kinds}
</section>"""
    return phone.page("Скачать по ссылке", body, heading="Скачать", top=phone.mode_switch(),
                      scripts=("phone-get",))
