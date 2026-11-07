"""(v5.44.87) «Скачать → По ссылке» — one field for every download.

The owner (6.11.2026): «все можно делать фильтрами одна строка ввода ссылки из ссылки будет понятно что это манга
манхва или видео … мне нужен мульти скачиватель а не разрозненная хуйня»; the look — the mockup he approved («Скачать»
on the design canvas: «вот теперь отлично делай»).

``/get`` (:func:`page`, in the frame of ``get_hub``) is one field: a link or a name.  What it is, the program says
(:func:`classify` — ``/api/get/what``, asked as the field is typed: the mark in the field):

* a gallery of nhentai (its link or its number) — «Хентай»: downloaded by ``manga_get`` (``/api/manga/download``);
* a title of a site of manga (``manhwa_sources.source_for``) — «Манга и манхва» («Хентай» — a site of hentai alone):
  found and downloaded by ``manhwa_get`` (its chapters as tiles, «Читать» without downloading — ``manga_online``);
* a booru post — «Галерея (booru)»: the grabber (``/grab``) opens it;
* any other link — «Видео»: found and downloaded by ``video_get`` — the other chat's: its answers are only read, nothing
  of its download changes (its full page, ``/videos/get``, stays: the card links to it);
* words — a name: the sites of manga searched at once (``manhwa_search``), the titles as covers, one picked — its card.

The card and «Скачать» are the computer's own machinery by its addresses, as the phone's «Скачать по ссылке» does
(``static/get-link.js``).  On the right — «Загрузки»: the downloads of the three going and their queues, «Пауза»,
«Остановить», the ones ended with «Открыть» (the nightly round of new chapters shows there too).  In «SFW» — no nhentai,
no sites of hentai, no booru (said in words, with «Показать всё»).
"""
from __future__ import annotations

from urllib.parse import quote

from webui.html import esc
from webui.server import Request, Response

MAX_TEXT = 4000
NAME_MAX = 120                       # a name searched (``manhwa_search.MAX_QUERY`` cuts it further)
LINK_ICON = ('<svg class="gl-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M10 14a5 5 0 0 0 '
             '7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1 1"/><path d="M14 10a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1-1"/>'
             '</svg>')


def register(router) -> None:
    router.add("POST", r"/api/get/what", what)


# ── what the text is ───────────────────────────────────────────────────────────────────────────────────────────────
def classify(text: str, settings: dict, now: str | None = None) -> dict:
    """{kind: "nhentai", ids} | {kind: "site", url, site, adult} | {kind: "booru", go} | {kind: "video", url} |
    {kind: "name", q} — each with ``label`` (the mark in the field); or {error} (``sfw`` — refused by the mode)."""
    from core import manga_download, manhwa_sources
    from webui import content_mode
    from webui.pages import get_hub
    from webui.pages.phone_get import first_link
    text = str(text or "").strip()[:MAX_TEXT]
    if not text:
        return {"error": "Вставьте ссылку или напишите название"}
    sfw = (now or content_mode.mode()) == "sfw"
    link = first_link(text)
    numbers, bad = manga_download.gallery_ids(text)
    if (bad or not numbers) and link:                    # a gallery's link among words
        numbers, bad = manga_download.gallery_ids(link)
    if numbers and not bad:
        if sfw:
            return {"error": "Это галерея nhentai, а включён режим «SFW» — они не скачиваются. «Показать всё» — и "
                             "скачиваются.", "sfw": True, "label": "Хентай"}
        if len(numbers) > manga_download.MAX_AT_ONCE:
            return {"error": f"За один раз — не больше {manga_download.MAX_AT_ONCE} галерей", "label": "Хентай"}
        return {"kind": "nhentai", "ids": numbers, "label": "Хентай · nhentai"}
    if link:
        source, _key = manhwa_sources.source_for(link)
        if source is not None:
            from webui.pages.manhwa_get import adult_refusal
            adult = bool(getattr(source, "adult_only", False))
            refused = adult_refusal(source, "sfw" if sfw else None)
            if refused:
                return {"error": refused, "sfw": True, "label": "Хентай"}
            return {"kind": "site", "url": link, "site": source.label, "adult": adult,
                    "label": ("Хентай · " if adult else "Манга и манхва · ") + source.label}
        if get_hub.kind_of(link, settings) == "booru":
            if sfw:
                return {"error": "Это пост booru-сайта — его скачивает «Галерея (booru)», а она скрыта в режиме "
                                 "«SFW». «Показать всё» — и она есть.", "sfw": True, "label": "Галерея (booru)"}
            return {"kind": "booru", "go": get_hub.address("gallery", link), "label": "Галерея (booru)"}
        return {"kind": "video", "url": link, "label": "Видео"}
    words = " ".join(text.split())
    if len(words) < 2:
        return {"error": "Напишите название — хотя бы две буквы, или вставьте ссылку"}
    return {"kind": "name", "q": words[:NAME_MAX], "label": "Название — поиск по сайтам"}


def what(req: Request) -> Response:
    answer = classify(str(req.json().get("text") or ""), req.settings)
    if "error" in answer:
        return Response.json({"ok": False, **answer}, 409 if answer.get("sfw") else 400)
    return Response.json({"ok": True, **answer})


# ── the page ───────────────────────────────────────────────────────────────────────────────────────────────────────
def page(req: Request) -> Response:
    """``/get``: ``?url=`` — the field (searched at once with ``go=1``; without — the page's own memory of its field: a
    reload, «назад»), ``?q=`` — a name searched at once.  A booru post's link — the grabber opens it."""
    from webui import content_mode
    from webui.pages import get_hub
    asked = (req.arg("url") or req.arg("q")).strip()[:2000]
    link = get_hub.one_link(asked)
    if link and get_hub.kind_of(link, req.settings) == "booru" and get_hub.shown("gallery"):
        return Response.redirect(get_hub.address("gallery", link))
    at_once = bool(asked) and (req.arg("go") == "1" or bool(req.arg("q").strip()))
    adult = content_mode.shows(True)
    merge = bool(req.settings.get("video_dub_merge", True))
    body = (f'<div class="gl" id="get-link" data-autofind="{"1" if at_once else ""}" data-adult="{"1" if adult else ""}"'
            f' data-merge="{"1" if merge else ""}">'
            f'<div class="gl-main">{_ask(req, asked, adult)}{_hits()}'
            f'<article class="gl-card" hidden aria-live="polite"></article></div>{_side()}</div>')
    return get_hub.frame(req, "Скачать по ссылке", body, part="link", kind="get-link", assets=("getlink",))


def _ask(req: Request, asked: str, adult: bool) -> str:
    from webui.pages import manhwa_get, phone_get
    sites = ", ".join((*(s.split(" (")[0] for s in manhwa_get.SITES), *(manhwa_get.ADULT_SITES if adult else ())))
    only = " и ".join(manhwa_get.LINK_ONLY)
    kinds = [f'<li><b>Манга и манхва</b> — тайтл на {esc(sites)}; по названию ищется сразу на всех, кроме '
             f'{esc(only)}</li>',
             f'<li><b>Видео</b> — страница тайтла или серии ({esc(phone_get._video_sites(req.settings))}); сайт не '
             f'пускает программу — его страница, сохранённая в браузере (Ctrl+S): <label class="gl-file"><input '
             f'class="gl-vh" type="file" accept=".html,.htm,.mhtml,.mht,.zip" data-saved-page="1"><span '
             f'class="gl-btn quiet small">Выбрать сохранённую страницу</span></label></li>']
    if adult:
        kinds.append("<li><b>Хентай</b> — галерея nhentai (ссылка или номер, можно несколько) и тайтл Хентай-тян, "
                     "HentaiLib или AllHentai</li>")
        kinds.append('<li><b>Посты booru-сайтов</b> — откроются в «Галерея (booru)»</li>')
    return f"""<section class="gl-ask" aria-labelledby="gl-title">
  <h1 id="gl-title">Скачать по ссылке</h1>
  <p class="gl-lead">Манга, манхва, хентай или видео — вставьте ссылку, программа сама поймёт, что это. Можно и
    название: найдёт на всех сайтах сразу.</p>
  <form class="gl-form" data-form="get-link" role="search">
    <label class="gl-vh" for="gl-field">Ссылка или название</label>
    <div class="gl-field">{LINK_ICON}<input id="gl-field" name="url" type="text" enterkeyhint="search" autocomplete="off"
      spellcheck="false" placeholder="Ссылка на тайтл, серию или галерею — или название" value="{esc(asked)}">
      <span class="gl-kind" hidden></span></div>
    <button type="submit" class="gl-go">Найти</button>
  </form>
  <div class="gl-step" role="status" aria-live="polite" hidden></div>
  <details class="gl-help">
    <summary>Какие ссылки понимает программа</summary>
    <ul class="gl-kinds">{"".join(kinds)}</ul>
    <p>Платные главы программа не покупает: глава, которая на сайте ссылки платная или которой там нет, берётся с
      другого сайта, где она бесплатная; нет нигде — программа ждёт и скачает сама, когда она станет бесплатной.
      Глава или серия, которая не скачалась с первого раза, пробуется ещё раз.</p>
    <p><a href="/settings#set-manga-sites">Адреса сайтов манги и вход</a></p>
  </details>
</section>"""


def _hits() -> str:
    return """<section class="gl-hits" hidden aria-labelledby="gl-hits-title">
  <div class="gl-hits-head"><span class="gl-spin" aria-hidden="true"></span><h2 id="gl-hits-title"></h2>
    <ul class="gl-hits-sites" aria-label="Что ответили сайты"></ul></div>
  <div class="gl-hits-list" aria-live="polite"></div>
  <p class="gl-hits-fold" hidden><button type="button" class="gl-btn quiet" data-hits-back></button></p>
</section>"""


def _side() -> str:
    return """<aside class="gl-side" aria-labelledby="gl-side-title">
  <div class="gl-side-head"><h2 id="gl-side-title">Загрузки</h2><span class="gl-side-count" aria-live="polite"></span></div>
  <ul class="gl-jobs" aria-label="Скачивание"></ul>
  <p class="gl-side-empty">Пока ничего не качается. Найдите тайтл, серию или галерею — «Скачать» поставит их сюда.</p>
  <div class="gl-side-buttons" hidden>
    <button type="button" class="gl-btn" data-all="pause">Пауза</button>
    <button type="button" class="gl-btn quiet" data-all="stop" title="Остановить все загрузки и убрать очередь">Остановить</button>
  </div>
  <p class="gl-side-note">Всё, что качается — манга, манхва, хентай, видео, — здесь. Новые главы, которые программа
    скачивает сама ночью, — тоже.</p>
</aside>"""


def link_of(url: str) -> str:
    """The address of this page with ``url`` looked for at once (the tabs' «Скачать», «Докачать сейчас»)."""
    return f"/get?url={quote(url, safe='')}&go=1"
