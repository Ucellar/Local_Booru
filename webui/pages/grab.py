"""The page «Граббер» — (v5.44.75) «Скачать → Галерея» (``get_hub.py``): search booru sites by tags (the parser's sites
that can do it), see what was found as previews — one card per file, «есть» when the library has it — open a post,
download one or many (originals only, tags of every site of the file), hide what you never want to see, subscribe to
the query.  The work is done by ``core/grabber``; downloads are subscription jobs."""
from __future__ import annotations

import json

from webui.html import CATEGORY_ORDER, CATEGORY_TITLES, esc
from webui.server import Request, Response

MAX_KEYS = 2000                  # cards one request may download or hide


def register(router) -> None:
    router.add("GET", r"/grab", grab_page)
    router.add("GET", r"/grab/media", grab_media)
    router.add("GET", r"/api/grab/card", grab_card)
    router.add("POST", r"/api/grab/search", grab_search)
    router.add("POST", r"/api/grab/more", grab_more)
    router.add("POST", r"/api/grab/download", grab_download)
    router.add("POST", r"/api/grab/hide", grab_hide)
    router.add("POST", r"/api/grab/owned", grab_owned)
    router.add("POST", r"/api/grab/subscribe", grab_subscribe)
    router.add("POST", r"/api/grab/options", grab_options)


def _prefs() -> dict:
    from webui import prefs
    stored = prefs.load()
    return {"thumbs": stored.get("thumbs", "m")}         # the look is the page's own (page_frame)


# ── the page ───────────────────────────────────────────────────────────────
# (v5.37) what to type, before the first search; the page hides it once there is anything found or said
_GRAB_TIPS = """<div class="empty grab-tips">
    <p><b>Что можно искать</b></p>
    <ul>
      <li>теги, как на самом сайте: <code>artist_name</code>, <code>blue_archive rating:general</code>, <code>-video</code></li>
      <li>ссылку на пост booru-сайта (или на его файл) — откроется этот пост одной карточкой</li>
    </ul>
    <p>Карточка — один файл; то, что уже есть в галерее, помечено. Скачиваются только оригиналы.</p>
  </div>"""
def grab_page(req: Request) -> Response:
    from core.grabber.search import searchable_sites
    s = req.settings
    sites = searchable_sites(s)
    query = " ".join(req.arg("q").split())
    asked = {x for x in req.arg("sites").split(",") if x}
    picks = "".join(
        f'<label class="check"><input type="checkbox" name="site" value="{esc(host)}"'
        f'{" checked" if not asked or host in asked else ""}> {esc(label)}</label>' for host, label in sites)
    hide = " checked" if s.get("grabber_preview_hide_existing", True) else ""
    no_sites = ('<span class="tool-warn">нет включённых сайтов с поиском по тегам — включите их на странице '
                '<a href="/sites">«Сайты»</a></span>')
    data = {"categories": [[c, CATEGORY_TITLES[c]] for c in CATEGORY_ORDER]}
    data_json = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    body = f"""
<section class="tool grab" id="grab" data-query="{esc(query)}">
  <div class="panel grab-panel">
    <form class="grab-search" data-form="grab-search" role="search">
      <input type="search" name="q" value="{esc(query)}" placeholder="теги, как на сайте: artist_name rating:explicit — или ссылка на пост"
        aria-label="Теги для поиска или ссылка на пост" spellcheck="false" autocomplete="off">
      <button type="submit" class="primary">Найти</button>
    </form>
    <div class="grab-options">
      <fieldset class="site-picks chips"><legend>Сайты</legend>{picks or no_sites}</fieldset>
      <label class="check grab-hide"><input type="checkbox" class="switch" name="hide_owned"{hide}> Скрывать то, что уже есть в галерее</label>
    </div>
  </div>
  {"" if query else _GRAB_TIPS}
  <p class="grab-status" role="status" aria-live="polite"></p>
  <div class="select-bar" hidden>
    <button type="button" data-action="select-mode" aria-pressed="false">Выбрать</button>
    <span class="select-tools" hidden>
      <span class="select-count">0 выбрано</span>
      <button type="button" class="link" data-action="select-page">все на странице</button>
      <button type="button" class="link" data-action="select-none">снять</button>
      <button type="button" class="primary" data-action="grab-download-selected" data-needs-selection disabled>Скачать выбранные</button>
      <button type="button" data-action="grab-hide-selected" data-needs-selection disabled>Скрыть выбранные</button>
    </span>
    <button type="button" data-action="grab-subscribe">Подписаться на этот запрос</button>
    <span class="action-status" role="status"></span>
  </div>
  <div class="grab-job" hidden><b class="grab-job-title"></b> <span class="grab-job-status"></span>
    <a href="/subs">журнал</a></div>
  <div class="thumbs grab-results"></div>
  <div class="grab-more tool-actions" hidden><button type="button" data-action="grab-more">Ещё</button>
    <span class="action-status" role="status"></span></div>
  <dialog class="grab-viewer" aria-labelledby="gv-title">
    <div class="gv-media"><img alt=""><p class="gv-note" hidden></p></div>
    <div class="gv-side">
      <h2 id="gv-title"></h2>
      <p class="gv-owned" hidden></p>
      <div class="tool-actions">
        <button type="button" class="primary" data-action="grab-download-one">Скачать</button>
        <button type="button" data-action="grab-hide-one">Скрыть</button>
        <button type="button" data-action="grab-viewer-close">Закрыть</button>
        <span class="action-status" role="status"></span>
      </div>
      <ul class="plain-list gv-sites"></ul>
      <div class="gv-tags"></div>
    </div>
    <button type="button" class="gv-nav gv-prev" data-action="grab-prev" aria-label="Предыдущий">‹</button>
    <button type="button" class="gv-nav gv-next" data-action="grab-next" aria-label="Следующий">›</button>
  </dialog>
  <script type="application/json" id="grab-data">{data_json}</script>
</section>"""
    sidebar = ('<section class="box grab-tags"><h3>Теги найденного</h3><div class="grab-tag-list">'
               '<p class="hint">Здесь появятся теги найденных постов.</p></div></section>')
    from webui.pages import get_hub
    return get_hub.frame(req, "Скачать в галерею", body, part="gallery", kind="grab", assets=("grab",),
                         sidebar=sidebar, body_data={"tool": "grab"}, **_prefs())


# ── cards for the page ─────────────────────────────────────────────────────
def _merged_groups(card: dict) -> list:
    """[(category, [tags])] of every site of the card; a tag counts once, in its first category."""
    seen, groups = set(), {}
    for item in card["entries"]:
        for category, names in (item.get("groups") or {}).items():
            for name in names or []:
                low = str(name).lower()
                if low in seen:
                    continue
                seen.add(low)
                groups.setdefault(str(category), []).append(str(name))
    order = [c for c in CATEGORY_ORDER if groups.get(c)] + [c for c in groups if c not in CATEGORY_ORDER and groups[c]]
    return [(c, groups[c]) for c in order]


def _card_json(card: dict, search=None) -> dict:
    first = card["entries"][0]
    sized = next((e for e in card["entries"] if e.get("width") and e.get("height")), first)
    tags = [[name, category] for category, names in _merged_groups(card) for name in names]
    return {"key": card["key"], "url": first.get("url") or "", "post": str(first.get("post_id") or ""),  # (v5.44.122)
            "sites": [e.get("label") or e["site"] for e in card["entries"]],
            "owned": card.get("owned") or "", "image_id": int(card.get("image_id") or 0),
            "w": int(sized.get("width") or 0), "h": int(sized.get("height") or 0), "ext": first.get("ext") or "",
            "video": bool(first.get("video")), "rating": first.get("rating") or "",
            "original": any(e.get("original") for e in card["entries"]), "tags": tags[:150],
            "blocked": search.blocked_tags(card) if search is not None and search.single else []}


def _batch(search, result: dict) -> dict:
    return {"ok": True, "search": search.state(), "cards": [_card_json(c, search) for c in result["cards"]],
            "updated": [_card_json(c, search) for c in result["updated"]], "removed": list(result["removed"])}


def _search_or_error(body_or_req) -> tuple:
    from core.grabber.search import get
    sid = body_or_req.get("s") if isinstance(body_or_req, dict) else body_or_req.arg("s")
    search = get(str(sid or ""))
    if search is None:
        return None, Response.json({"ok": False, "error": "Поиск устарел — нажмите «Найти» ещё раз"}, 410)
    return search, None


def grab_search(req: Request) -> Response:
    """A tag search on the chosen sites — or, for a web address, the one post it points at (any known site)."""
    from core.grabber.link import LinkError, looks_like_link
    from core.grabber.search import open_link, open_search, searchable_sites
    body = req.json()
    query = " ".join(str(body.get("q") or "").split())
    if not query:
        return Response.json({"ok": False, "error": "Введите теги для поиска или ссылку на пост"}, 400)
    if looks_like_link(query):
        try:
            search = open_link(dict(req.settings), query)
        except LinkError as exc:
            return Response.json({"ok": False, "error": str(exc)}, 400)
        batch = _batch(search, search.more())
        if not batch["cards"]:
            lane = next(iter(search.state()["sites"].values()), {})
            why = lane.get("error") or "на сайте нет такого поста (или его видно только после входа — «Сайты»)"
            batch["error_note"] = f"Пост не получен: {why}"
        return Response.json(batch)
    known = {host for host, _label in searchable_sites(req.settings)}
    hosts = [str(h) for h in body.get("sites") or [] if str(h) in known]
    if not hosts:
        return Response.json({"ok": False, "error": "Выберите хотя бы один сайт"}, 400)
    search = open_search(dict(req.settings), query, hosts, hide_owned=bool(body.get("hide_owned")))
    return Response.json(_batch(search, search.more()))


def grab_more(req: Request) -> Response:
    search, error = _search_or_error(req.json())
    if error:
        return error
    return Response.json(_batch(search, search.more()))


def grab_media(req: Request) -> Response:
    from core.grabber.media import MediaError, media
    search, error = _search_or_error(req)
    if error:
        return error
    try:
        data, ctype = media(search, req.arg("k"), "sample" if req.arg("v") == "sample" else "preview")
    except MediaError as exc:
        return Response.json({"ok": False, "error": str(exc)}, exc.status)
    return Response(200, data, ctype, {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


def grab_card(req: Request) -> Response:
    """A card for the post view: tags by category (Gelbooru-type sites are asked for categories
    now), the sites with their post pages."""
    from urllib.parse import quote
    search, error = _search_or_error(req)
    if error:
        return error
    card = search.card(req.arg("k"))
    if card is None:
        return Response.json({"ok": False, "error": "Такой карточки нет в этом поиске"}, 404)
    for item in card["entries"]:
        groups = item.get("groups") or {}
        flat = not any(v for k, v in groups.items() if k != "general")
        site = search.site(item["site"])
        if flat and site is not None and hasattr(site, "categorize") and not item.get("categorized"):
            try:
                item["groups"] = site.categorize(list(item.get("tags") or []))
            except Exception as exc:     # categories are a nicety: the flat tags stay
                search._note(f"{item['site']}: категории тегов не получены: {type(exc).__name__}")
            item["categorized"] = True
    entries = [{"site": e["site"], "label": e.get("label") or e["site"], "url": e.get("url") or "",
                "rating": e.get("rating") or "", "ext": e.get("ext") or "", "w": int(e.get("width") or 0),
                "h": int(e.get("height") or 0), "original": bool(e.get("original"))} for e in card["entries"]]
    sample = f"/grab/media?s={quote(search.id)}&k={quote(card['key'])}&v=sample"
    return Response.json({"ok": True, "card": _card_json(card, search), "entries": entries, "sample": sample,
                          "groups": [[c, CATEGORY_TITLES.get(c, c), names] for c, names in _merged_groups(card)]})


def _keys(body: dict) -> list:
    raw = body.get("keys")
    return [str(k) for k in raw[:MAX_KEYS]] if isinstance(raw, list) else []


def grab_download(req: Request) -> Response:
    from core import subscription_jobs
    from core.subscription_forms import grab_download as make_sub
    body = req.json()
    search, error = _search_or_error(body)
    if error:
        return error
    cards = [c for c in (search.card(k) for k in _keys(body)) if c is not None]
    if not cards:
        return Response.json({"ok": False, "error": "Не выбрано ни одной карточки"}, 400)
    blocked = sorted({t for c in cards for t in search.blocked_tags(c)})
    if blocked:            # only a link shows such a card; the download would skip it without a word
        return Response.json({"ok": False, "error": "У поста теги из блоклиста подписок и «Галереи (booru)»: "
                              + ", ".join(blocked[:8]) + " — такой пост не скачивается. Блоклист — на странице «Подписки»."},
                             409)
    if not any(e.get("original") for c in cards for e in c["entries"]):
        return Response.json({"ok": False, "error": "У выбранных постов сайты не дают оригинал — скачивать нечего"}, 400)
    sub = make_sub(search.query, cards, [s.host for s in search.sites])
    from webui.pages.post_sets import FILES, plural          # (v5.44.116) the section's name, not «Граббер: … файл(ов)»
    label = f"Галерея (booru): {plural(len(cards), FILES)} — {search.query}"
    try:
        job = subscription_jobs.start(dict(req.settings), [sub], label=label, kind="grab", forget_seeds=True)
    except subscription_jobs.SubscriptionsBusy as exc:
        return Response.json({"ok": False, "error": f"{exc}. Когда это закончится, нажмите ещё раз."}, 409)
    return Response.json({"ok": True, "id": job.id, "count": len(cards)})


def grab_hide(req: Request) -> Response:
    body = req.json()
    search, error = _search_or_error(body)
    if error:
        return error
    done = search.hide(_keys(body))
    if not done:
        return Response.json({"ok": False, "error": "Не выбрано ни одной карточки"}, 400)
    return Response.json({"ok": True, "hidden": done})


def grab_owned(req: Request) -> Response:
    search, error = _search_or_error(req.json())
    if error:
        return error
    search.refresh_owned()
    return Response.json({"ok": True, "marks": {c["key"]: {"owned": c.get("owned") or "", "image_id": int(c.get("image_id") or 0)}
                                                for c in search.cards}})


def grab_subscribe(req: Request) -> Response:
    """A subscription to the search's query on its sites (in the table's order)."""
    from core.subscription_forms import scannable_sites
    from core.subscriptions import add_subscription, load_subscriptions
    search, error = _search_or_error(req.json())
    if error:
        return error
    can_scan = set(scannable_sites())            # a subscription pages through the site's API by tag
    hosts = [s.host for s in search.sites if s.host in can_scan]
    if not hosts:
        return Response.json({"ok": False, "error": "Подписки не умеют проверять сайты этого поиска"}, 400)
    for sub in load_subscriptions():
        if " ".join(str(sub.get("query") or "").split()) == search.query:
            return Response.json({"ok": True, "id": sub.get("id"), "existing": True})
    sites = [{"site": host, "priority": max(1, 5 - i)} for i, host in enumerate(hosts)]
    sub = add_subscription(search.query, sites, search.query)
    return Response.json({"ok": True, "id": sub["id"], "existing": False})


def grab_options(req: Request) -> Response:
    """«Скрывать то, что уже есть» is remembered (the same setting as the old grabber's)."""
    from webui import settings_store
    if not settings_store.save({"grabber_preview_hide_existing": bool(req.json().get("hide_owned"))}):
        return Response.json({"ok": False, "error": "Программа не ответила — не сохранено"}, 503)
    return Response.json({"ok": True})

