// Local Booru — (v5.44.121) one page of a series and one player of an episode (webui/pages/video_series.py; the owner's
// mockup D «Видео: одна страница сериала и один плеер»).  The page gets the series as a model (inline, then
// /api/series while its site is read): «Откуда серии» (the folder, «Смотреть онлайн», the voice-over of the series),
// the episodes — the folder's and the site's in one list, each with its variants (▾), «Смотреть» / «Продолжить» —, and
// the player under them.  An episode of the folder plays its file from the disk, an episode of the site — its stream
// through the program (/api/series/play); what was watched goes to the one memory (/api/series/progress).
// In the program's window an episode is a page of its own (?ep=…&play=1: the window's own player, booru.js
// nativeVideo); in a browser and on the phone the same <video> takes the next episode (the bar of the page: ⏮ ⏭, the
// number, the opening and the ending on it).
// (v5.44.142; the owner's mockup «Видео: серии плитками и скачивание озвучек» — «да отлично») The episodes are tiles
// over the player (no list of rows, no row of numbers over it): in the folder / only on the site / watched / new / where
// it stopped, the voice-over of the series over them; «Скачать серии…» turns them into the choice of «Скачать» of the
// page of before — «Дубляж / Озвучка / Субтитры», several studios at once, «Качество», «в один файл», the weight
// (/api/videos/find → find-state, measure, download); what downloads for the series goes on its tiles
// (/api/series/getting, polled).
(() => {
  "use strict";
  const page = document.getElementById("series-page");
  if (!page) return;
  const LB = window.LocalBooru || {};
  const phone = page.dataset.phone === "1";
  const inApp = page.dataset.app === "1";
  const view = page.dataset.view || "page";
  const modelNode = page.querySelector("[data-sv-model]");
  let M = {};
  try { M = JSON.parse(modelNode ? modelNode.textContent : "{}") || {}; } catch (_) { M = {}; }
  const $ = (sel) => page.querySelector(sel);
  const ICONS = {
    play: "M8 5v14l11-7z", check: "M5 12l5 5L20 7", globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18",
    folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", chev: "M6 9l6 6 6-6",
    voice: "M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21",
    videos: "M3 6h18v12H3zM10 9l5 3-5 3z", forth: "M9 5l7 7-7 7", back: "M15 5l-7 7 7 7",
    down: "M12 3v12M7 10l5 5 5-5M5 21h14",
  };

  // ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function icon(name, size) {
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("width", String(size || 14));
    s.setAttribute("height", String(size || 14));
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("class", "sv-i");
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", ICONS[name]);
    s.append(p);
    return s;
  }
  function plural(n, forms) {
    const a = Math.abs(n) % 100, b = a % 10;
    return n + " " + (a > 10 && a < 20 ? forms[2] : b > 1 && b < 5 ? forms[1] : b === 1 ? forms[0] : forms[2]);
  }
  const EPISODES = ["серия", "серии", "серий"];
  function clock(value) {
    const t = Math.max(0, Math.floor(Number(value) || 0));
    const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0");
  }
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  function failure(response, data) {
    const error = new Error((data && data.error) || (response.status === 503 ? "Программа занята — попробуйте через минуту"
      : "Программа ответила ошибкой " + response.status));
    error.data = data || {};
    return error;
  }
  async function getJson(path) {
    let response;
    try { response = await fetch(path, { credentials: "same-origin" }); } catch (_) { throw new Error("Нет связи с программой"); }
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) throw failure(response, data);
    return data;
  }
  async function post(path, body) {
    let response;
    try {
      response = await fetch(path, { method: "POST", credentials: "same-origin",
                                     headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    } catch (_) { throw new Error("Нет связи с программой"); }
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) throw failure(response, data);
    return data;
  }
  function say(text, bad) {
    if (LB.say) LB.say(null, text, !!bad);
  }
  // a word of «Серии» — in its own line (the hero's status line may be out of sight); the phone — its note
  let toldTimer = 0;
  function tell(text, bad) {
    const line = $("[data-sv-said]");
    if (!line || phone) { say(text, bad); return; }
    clearTimeout(toldTimer);
    line.textContent = text || "";
    line.classList.toggle("bad", !!bad);
    line.hidden = !text;
    if (text) toldTimer = setTimeout(() => { line.hidden = true; }, bad ? 20000 : 9000);
  }
  const ref = () => (M.series ? { series: M.series } : { url: M.url || page.dataset.url || "" });
  function query(extra) {
    const pairs = M.series ? [["series", M.series]] : [["url", M.url || page.dataset.url || ""]];
    Object.entries(extra || {}).forEach(([k, v]) => pairs.push([k, v]));
    return pairs.map(([k, v]) => k + "=" + encodeURIComponent(v)).join("&");
  }
  function address(key, play) {
    const m = phone ? "/m" : "";
    const tail = (key ? "ep=" + encodeURIComponent(key) : "") + (play ? (key ? "&" : "") + "play=1" : "");
    if (M.series) return m + "/videos/s/" + M.series + (tail ? "?" + tail : "") + (phone ? "" : "#player");
    return m + "/videos/watch?url=" + encodeURIComponent(M.url || page.dataset.url || "") + (tail ? "&" + tail : "") +
      (phone ? "" : "#player");
  }
  const episodes = () => M.episodes || [];
  const entryOf = (key) => episodes().find((e) => e.key === String(key)) || null;
  function neighbour(key, step) {
    const list = episodes();
    const at = list.findIndex((e) => e.key === key);
    if (at < 0) return null;
    for (let i = at + step; i >= 0 && i < list.length; i += step) {
      if (list[i].disk.length || list[i].site.length) return list[i];
    }
    return null;
  }
  const playable = (e) => !!(e && (e.disk.length || e.site.length));
  const nameOf = (e) => e.name + (e.title ? " — «" + e.title + "»" : "");
  const siteName = () => (M.site && M.site.name) || "сайта";

  function hiddenByMode() {
    const words = document.querySelector("template[data-sv-hidden]");
    if (!words) return false;
    stopAll();
    document.title = "Скрыто — Local Booru";
    page.replaceWith(words.content.cloneNode(true));
    return true;
  }

  // ── «←»: back where the page was opened from (a title of a site — the feed, as «Смотреть» did) ─────────────────
  const programBefore = () => !!(LB.programBefore && LB.programBefore());
  function feedEntry() {
    try {
      const nav = window.navigation;
      if (!nav || !nav.currentEntry || typeof nav.entries !== "function") return null;
      const list = nav.entries();
      for (let i = nav.currentEntry.index - 1; i >= 0; i--) {
        const entry = list[i];
        let path = "";
        try {
          const state = entry.getState();
          path = (state && state.lbFeed) || "";
        } catch (_) { path = ""; }
        if (!path && entry.url) {
          const from = new URL(entry.url);
          path = from.origin === location.origin ? from.pathname : "";
        }
        if (path === page.dataset.back) return entry;
      }
    } catch (_) { /* the link */ }
    return null;
  }
  function toFeed(link) {
    const path = page.dataset.back;
    let target = link.getAttribute("href") || path;
    try {
      const last = JSON.parse(sessionStorage.getItem("lb-feed-last") || "{}") || {};
      if (typeof last[path] === "string" && (last[path] === path || last[path].startsWith(path + "?"))) target = last[path];
      sessionStorage.setItem("lb-feed-return", JSON.stringify({ path, at: Date.now() }));
    } catch (_) { /* the feed as the server gives it */ }
    location.assign(target);
  }
  const feed = /\/feed$/.test(page.dataset.back || "");
  if (view === "page" && !feedEntry() && programBefore()) {
    document.querySelectorAll("[data-sv-back]").forEach((back) => {
      back.textContent = "← Назад";
      back.title = "На прошлую страницу программы";
    });
  }
  document.querySelectorAll("[data-sv-back]" + (feed ? ", .m-top [data-back]" : "")).forEach((back) => {
    back.addEventListener("click", (event) => {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const entry = feed ? feedEntry() : null;
      if (!entry) {
        if (back.matches("[data-sv-back]") && programBefore()) { event.preventDefault(); history.back(); return; }
        if (!feed) return;
        event.preventDefault();
        toFeed(back);
        return;
      }
      event.preventDefault();
      try {
        const going = window.navigation.traverseTo(entry.key);
        if (going && going.committed) going.committed.catch(() => toFeed(back));
        if (going && going.finished) going.finished.catch(() => {});
      } catch (_) {
        toFeed(back);
      }
    });
  });

  // ── the hero: what the site gives a title that has no folder here ─────────────────────────────────────────────
  function hero() {
    const name = $("[data-sv-name]");
    if (name && M.title && !M.series) {
      name.textContent = M.title;
      document.title = M.title + " — Local Booru";
    }
    const facts = $("[data-sv-facts]");
    if (facts && M.facts && !facts.textContent.trim()) facts.textContent = M.facts;
    const alt = $("[data-sv-alt]");
    if (alt && (M.alt || []).length && !alt.textContent.trim()) {
      alt.textContent = M.alt.join(" · ");
      alt.hidden = false;
    }
    const cover = $("[data-sv-cover]");
    if (cover && M.poster && !M.series && !cover.querySelector("img[data-sv-poster]")) {
      const img = el("img");
      img.alt = "";
      img.decoding = "async";
      img.dataset.svPoster = "";
      const old = cover.querySelector("img");
      img.addEventListener("load", () => { img.classList.add("lb-in"); if (old) old.remove(); }, { once: true });
      img.addEventListener("error", () => img.remove(), { once: true });
      cover.append(img);
      img.src = M.poster;
    }
    const desc = $("[data-sv-desc]");
    if (desc && desc.hidden && (M.about || []).length) {
      desc.textContent = "";
      const text = el("div", "sv-desc-text");
      M.about.forEach((p) => text.append(el("p", "", p)));
      desc.append(text);
      desc.hidden = false;
      const from = $("[data-sv-from]");
      if (from && M.about_from) { from.textContent = M.about_from; from.hidden = false; }
    }
    // (v5.44.129) a genre — a search, as the server draws it (video_series.chip_href): a title of a site (the page
    // goes back to «Лента») — «Лента» with the genre chosen, (v5.44.148) one the feed has not — the feed's search by
    // the word; a series of the folder — the folder's series by it; a name the program makes no search of — words only
    const chips = $("[data-sv-chips]");
    const named = M.chips || (M.genres || []).map((name) => ({ name }));
    if (chips && !chips.children.length && named.length) {
      const fromFeed = /\/videos\/feed$/.test(page.dataset.back || "");
      named.forEach((c) => {
        const li = el("li");
        const feed = fromFeed && c.g;
        const href = c.never ? "" : feed ? (phone ? "/m" : "") + "/videos/feed?g=" + encodeURIComponent(c.g)
          : fromFeed ? (phone ? "/m" : "") + "/videos/feed?q=" + encodeURIComponent(c.name)
            : (phone ? "/m/videos?q=" : "/videos?tags=") + encodeURIComponent(c.name);
        if (href) {
          const a = el("a", "", c.name);
          a.href = href;
          a.title = feed ? "Найти в «Ленте»" : fromFeed ? "Искать в «Ленте» по слову" : "Искать в папке видео";
          li.append(a);
        } else {
          li.className = "sv-chip-words";
          li.textContent = c.name;
        }
        chips.append(li);
      });
    }
    const more = desc && desc.querySelector("[data-sv-more]");
    const text = desc && desc.querySelector(".sv-desc-text");
    if (more && text) more.hidden = text.scrollHeight <= text.clientHeight + 2;
    const waiting = $("[data-sv-waiting]");          // «Читаю тайтл с …»: the site has answered (or said why not)
    if (waiting && !(M.site && M.site.pending)) waiting.remove();
    siteFields();
  }
  // what the site says of its title (its ratings with the votes, its facts, the voice-overs), folded
  function siteFields() {
    const about = $(".sv-about");
    if (!about) return;
    let box = about.querySelector(".sv-site-fields");
    const rows = M.site_fields || [];
    if (!rows.length) { if (box) box.remove(); return; }
    if (!box) {
      box = el("details", "sv-fields sv-site-fields");
      box.append(el("summary", "", "С " + siteName() + ": оценки, факты, озвучки"));
      const order = about.querySelector(".vorder");
      if (order) about.insertBefore(box, order); else about.append(box);
    }
    let list = box.querySelector("dl");
    if (!list) { list = el("dl", "vfacts"); box.append(list); }
    list.textContent = "";
    rows.forEach(([label, words]) => list.append(el("dt", "", label), el("dd", "", words)));
  }
  // ── «Порядок просмотра» of a title from YummyAnime: its seasons, films and extras in the order to watch them — the
  // ones in the library open there, the others open their own page (watched there, downloaded there).  (v5.44.145)
  // For a title of a site as for a series of the folder, on the computer and on the phone: the one page of 5.44.121
  // drew it only for a series of the folder on the computer (the owner: «и почему пропал порядок просмотра?»).
  // /api/videos/order/<id> — a series of the folder (with its ratings as they are now), /api/videos/order?url= — a title.
  function setupOrder(box) {
    const ask = box.dataset.order ? "/api/videos/order/" + encodeURIComponent(box.dataset.order)
      : "/api/videos/order?url=" + encodeURIComponent(box.dataset.orderUrl || "");
    const base = phone ? "/m" : "";
    fetch(ask, { credentials: "same-origin" }).then((r) => r.json()).then((reply) => {
      if (box.dataset.order) showRatings((reply && reply.ratings) || []);
      const order = (reply && reply.order) || [];
      if (order.length < 2) return;
      const toggle = el("button", (phone ? "m-button m-quiet" : "vbutton primary") + " vorder-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "false");
      toggle.append(el("span", "", "Порядок просмотра"), el("span", "vorder-count", plural(order.length, ["часть", "части", "частей"])));
      const arrow = el("span", "vorder-arrow", "▾");
      arrow.setAttribute("aria-hidden", "true");
      toggle.append(arrow);
      const list = el("ol", "vorder-list");
      list.hidden = true;
      for (const o of order) {
        const li = el("li", o.here ? "here" : o.series ? "have" : o.soon ? "soon" : "");
        let name;
        if (o.series && !o.here) {
          name = el("a", "vorder-name", o.title);
          name.href = base + "/videos/s/" + encodeURIComponent(o.series);
        } else if (o.watch && !o.here && !o.soon) {
          name = el("a", "vorder-name", o.title);
          name.href = base + o.watch;
        } else {
          name = el(o.here ? "b" : "span", "vorder-name", o.title);
        }
        li.append(name);
        const about = [o.type, o.kind, o.year].filter(Boolean).join(", ");
        if (about) li.append(document.createTextNode(" — " + about));
        const mark = el("span", "vorder-mark");
        if (o.here) mark.textContent = "это";
        else if (o.series) mark.textContent = "есть" + (o.episodes ? ": " + plural(o.episodes, EPISODES) : "");
        else if (o.soon) mark.textContent = "анонс";
        else if (o.get) {
          const get = el("a", "", "найти и скачать");
          get.href = base + o.get;
          mark.append(get);
        }
        li.append(mark);
        list.append(li);
      }
      toggle.addEventListener("click", () => {
        list.hidden = !list.hidden;
        toggle.setAttribute("aria-expanded", String(!list.hidden));
      });
      box.append(toggle, list);
      box.hidden = false;
    }).catch(() => { /* the site did not answer: the page without its order */ });
  }
  // the title's ratings as YummyAnime has them now (its own people's first): «Оценки» of the folder's series written then
  function showRatings(list) {
    const facts = page.querySelector(".sv-hero .vfacts");
    if (!facts || !list.length) return;
    let dd = null;
    for (const dt of facts.querySelectorAll("dt")) if (dt.textContent.trim() === "Оценки") dd = dt.nextElementSibling;
    if (!dd) {
      const dt = el("dt", "", "Оценки");
      dd = el("dd");
      const files = [...facts.querySelectorAll("dt")].find((x) => x.textContent.trim() === "Файлов");
      if (files) { facts.insertBefore(dt, files); facts.insertBefore(dd, files); } else facts.append(dt, dd);
    }
    dd.textContent = "";
    list.forEach(([name, value], n) => {
      if (n) dd.append(" · ");
      dd.append(name + " ");
      dd.append(el("b", "", value));
    });
  }
  page.querySelectorAll(".vorder[data-order], .vorder[data-order-url]").forEach(setupOrder);

  page.addEventListener("click", (event) => {
    const more = event.target.closest("[data-sv-more]");
    if (!more) return;
    const desc = more.closest("[data-sv-desc]");
    const open = desc.classList.toggle("open");
    more.textContent = open ? "Свернуть" : "Ещё";
  });

  function numbers() {
    const holder = $("[data-sv-numbers]");
    if (!holder) return;
    holder.textContent = "";
    const c = M.counts || {};
    if (M.rating) holder.append(el("span", "sv-rate", "★ " + M.rating));
    if (!M.single) holder.append(el("span", "", plural(c.total || 0, EPISODES)));
    if (M.site && M.site.online && M.site.name && (M.dubs || []).length) holder.append(el("span", "", "с " + M.site.name));
    if (M.series && !M.single) holder.append(el("span", "sv-have", "в папке: " + (c.disk || 0)));
    if (c.done) holder.append(el("span", "", "досмотрено: " + c.done));
  }

  function actions() {
    const holder = $("[data-sv-actions]");
    if (!holder) return;
    holder.textContent = "";
    const r = M.resume || {};
    const target = entryOf(r.key);
    if (target && playable(target)) {
      const go = el("a", phone ? "m-button sv-go wide" : "sv-btn primary sv-go");
      go.href = address(target.key, true);
      go.dataset.svGo = "";
      go.append(icon("play", 16), el("span", "", r.words));
      go.addEventListener("click", (event) => {
        if (navigates()) return;
        event.preventDefault();
        choose(target.key, { autoplay: true, at: r.at });
      });
      holder.append(go);
      const first = episodes().find(playable);
      if (first && !M.single && (first.key !== target.key || r.at > 0)) {
        const again = el("a", phone ? "m-button m-quiet" : "sv-btn", phone ? "С начала" : "Смотреть с начала");
        again.href = address(first.key, true);
        again.dataset.svStart = "";
        again.addEventListener("click", (event) => {
          if (navigates()) return;
          event.preventDefault();
          choose(first.key, { autoplay: true, at: 0 });
        });
        holder.append(again);
      }
    }
    // (v5.44.142) «Скачать серии…»: several voice-overs and subtitles at once, «Качество», «в один файл» — on the tiles
    if (M.get && M.get.url && !M.single) {
      const get = el("button", phone ? "m-button m-quiet sv-get-open" : "sv-btn sv-get-open");
      get.type = "button";
      get.dataset.svGetOpen = "";
      get.setAttribute("aria-haspopup", "dialog");
      get.title = "Выбрать озвучки, субтитры, качество и серии — скачать в папку сериала";
      get.append(icon("down", 16), el("span", "", "Скачать серии…"));
      get.addEventListener("click", openGet);
      holder.append(get);
    }
    if (M.series && !phone) {
      const reveal = el("button", "sv-btn quiet");
      reveal.type = "button";
      reveal.dataset.action = "vid-reveal";
      reveal.dataset.id = M.series;
      reveal.append(icon("folder", 16), el("span", "", "Показать в папке"));
      holder.append(reveal);
    }
  }

  // ── «Откуда серии» ────────────────────────────────────────────────────────────────────────────────────────────
  function voiceLine(v) {
    const parts = [];
    if (v.disk.length) parts.push("в папке — " + (v.disk.length === episodes().length ? "все " + v.disk.length : "серии " + ranges(v.disk)));
    if (v.site.length) parts.push("на сайте — " + (v.site.length === episodes().length ? "все " + v.site.length : "серии " + ranges(v.site)));
    return parts.join(", ");
  }
  function ranges(labels) {
    const nums = labels.map(Number);
    if (nums.some((n) => !isFinite(n))) return labels.slice(0, 6).join(", ") + (labels.length > 6 ? "…" : "");
    nums.sort((a, b) => a - b);
    const out = [];
    let start = nums[0], prev = nums[0];
    for (const n of nums.slice(1)) {
      if (n === prev + 1) { prev = n; continue; }
      out.push(start === prev ? String(start) : start + "–" + prev);
      start = prev = n;
    }
    if (nums.length) out.push(start === prev ? String(start) : start + "–" + prev);
    return out.join(", ");
  }
  const voiceKey = () => String(M.voice_key || "");          // the server's key of the voice-over chosen
  function currentVoice() {
    const key = voiceKey();
    const all = M.voices || [];
    return all.find((v) => key && v.key === key) || null;
  }

  function sources() {
    const holder = $("[data-sv-src]");
    if (!holder) return;
    holder.textContent = "";
    // (v5.44.142) under the tiles: where the episodes come from (their numbers — the legend over the tiles; the
    // voice-over of the series — the choice over them)
    const h = el("h3", "sv-src-h");
    h.append(icon("videos", 15), el("span", "", "Откуда серии"));
    holder.append(h);
    const rows = el("dl", "sv-src-rows");
    const row = (label, cls) => {
      const wrap = el("div", "sv-src-row" + (cls ? " " + cls : ""));
      wrap.append(el("dt", "", label));
      const dd = el("dd");
      wrap.append(dd);
      rows.append(wrap);
      return dd;
    };
    if (M.series) {
      const dd = row("Папка", "disk");
      const line = el("p", "sv-src-line");
      line.append(icon("folder", 14));
      const words = el("span");
      words.append("«" + (M.folder.name || M.title) + "»" + (M.single ? "" : " — "));
      if (!M.single) words.append(el("b", "", M.folder.count ? "серии " + M.folder.range : "серий в папке нет"));
      if (!phone) {
        words.append(" · ");
        const show = el("button", "sv-link", "Показать папку");
        show.type = "button";
        show.dataset.action = "vid-reveal";
        show.dataset.id = M.series;
        words.append(show);
      }
      line.append(words);
      dd.append(line);
      const variants = (M.folder && M.folder.variants) || [];
      if (variants.length) {
        const vline = el("p", "sv-src-line sv-muted");
        vline.append(icon("voice", 14));
        const names = variants.map((v) => "«" + v.name + "» (" + plural(v.files, ["файл", "файла", "файлов"]) + ")");
        const span = el("span", "", (variants.length > 1 ? "подпапки " : "подпапка ") + names.join(" и ") + " — ");
        span.append(el("b", "", "варианты серий " + (M.folder.varied || "")), ", а не отдельные серии");
        vline.append(span);
        dd.append(vline);
      }
    }
    const site = M.site || {};
    if (site.can || M.series) {
      const dd = row("Онлайн", "site");
      if (site.can) {
        const label = el("label", "sv-check");
        const box = el("input");
        box.type = "checkbox";
        box.checked = !!site.online;
        box.dataset.svOnline = "";
        const words = el("span");
        words.append(el("b", "", "Смотреть онлайн"));
        const missing = site.online && site.missing ? " (" + site.missing + ")" : "";
        words.append(": " + (M.series ? "серии, которых нет в папке" + missing + ", — " : "серии ") + "с " + site.name +
          ", без скачивания, в этом же плеере");
        label.append(box, words);
        box.addEventListener("change", () => chooseOnline(box.checked));
        dd.append(label);
        const where = el("p", "sv-src-line sv-muted");
        where.append(icon("globe", 14));
        const text = el("span", "", (M.series ? "адрес сайта — из «Описание с сайта»: " : "") +
          String(site.address || "").replace(/^https?:\/\//, ""));
        if (M.series && !phone) {
          text.append(" · ");
          const change = el("button", "sv-link", "Изменить");
          change.type = "button";
          change.dataset.action = "vid-meta-open";
          text.append(change);
        }
        where.append(text);
        dd.append(where);
        if (site.online) {
          if (site.hidden) dd.append(el("p", "sv-src-state", "Серии сайта скрыты: включён режим «" + site.hidden.toUpperCase() + "»"));
          else if (site.pending) dd.append(el("p", "sv-src-state", (site.said || "Читаю серии с " + site.name).replace(/…$/, "") + "…"));
          else if (site.error) {
            const bad = el("p", "sv-src-state bad", site.error + " ");
            const again = el("button", "sv-link", "Спросить ещё раз");
            again.type = "button";
            again.addEventListener("click", () => { M.site.error = ""; M.site.pending = true; sources(); waitSite(true); });
            bad.append(again);
            dd.append(bad);
          }
        }
      } else {
        const none = el("p", "sv-src-line sv-muted");
        none.append(icon("globe", 14));
        // in «SFW» the sites of hentai are not named (v5.44.116)
        const sites = document.documentElement.dataset.contentMode === "sfw" ? "YummyAnime" : "YummyAnime, Hentasis, HentaiHaven, Hentai-XS и HFShare";
        const span = el("span", "", `Смотреть онлайн можно с ${sites}: возьмите описание тайтла с сайта — его адрес станет адресом серий. `);
        if (!phone) {
          const open = el("button", "sv-link", "Указать адрес");
          open.type = "button";
          open.dataset.action = "vid-meta-open";
          span.append(open);
        }
        none.append(span);
        dd.append(none);
      }
    }
    holder.append(rows);
  }

  // ── the episodes: tiles over the player (v5.44.142) ──────────────────────────────────────────────────────────
  // the number; ✓ watched / ● new in the corner; under it «▶ 12:34» where it stopped, «досмотрена», «новая», «в папке»;
  // the episode in the player framed; a file of it downloading — how far («62%», «в очереди», «✓ скачана»)
  let newestFirst = false;
  try { newestFirst = localStorage.getItem("lb-sv-order") === "new"; } catch (_) { newestFirst = false; }
  const PAGE = 100;                      // a long series: its tiles a hundred at a time («1–100», «101–200» …)
  let hundred = -1;                      // the hundred shown (-1: the one of the episode in the player)
  let tilesShown = null;                 // (v5.44.158) which tiles the list showed last, and when they began to come
  let tilesAt = 0;
  const wasDone = new Map();             // (v5.44.158) an episode -> watched to the end when its tile was drawn last
  let going = null;                      // what downloads for the series now (/api/series/getting)
  const FILES = ["файл", "файла", "файлов"];

  function parts(count) {                // [[first, last]] of the hundreds of a long list (one part — a short one)
    if (count <= PAGE + PAGE / 5) return [[0, Math.max(0, count - 1)]];
    const out = [];
    for (let i = 0; i < count; i += PAGE) out.push([i, Math.min(count, i + PAGE) - 1]);
    return out;
  }
  function hundreds(labels, now, choose) {   // the buttons of the hundreds: «1–100», «101–200» …
    const nav = el("div", "sv-parts");
    nav.setAttribute("role", "group");
    nav.setAttribute("aria-label", "Серии по сотне");
    parts(labels.length).forEach(([a, b], n) => {
      const button = el("button", "sv-part", labels[a] + "–" + labels[b]);
      button.type = "button";
      button.setAttribute("aria-pressed", n === now ? "true" : "false");
      button.addEventListener("click", () => choose(n));
      nav.append(button);
    });
    return nav;
  }

  const DL = { active: "скачивается", paused: "пауза", queued: "в очереди", done: "✓ скачана", failed: "не скачалась",
               stopped: "остановлена" };
  function dlOf(e) {                     // the episode in the download going for the series, or in the queue
    const g = going && going.going;
    const slot = g && g.labels ? g.labels[e.key] : null;
    if (slot) {
      const pct = Math.round(100 * (Number(slot.part) || 0));
      const sub = slot.state === "active" ? pct + "%" : slot.state === "paused" ? "пауза · " + pct + "%" : DL[slot.state] || "";
      return { state: slot.state, part: Number(slot.part) || 0, sub,
               words: slot.state === "active" ? "скачивается, " + pct + "%" : sub.replace("✓ ", "") };
    }
    const q = going && going.queued;
    if (q && (q.labels || []).includes(e.key)) return { state: "queued", part: 0, sub: "в очереди", words: "в очереди на скачивание" };
    return null;
  }
  function tileWords(e) {                // its tip and what a screen reader says (video_series.tile_words — the same)
    const words = [nameOf(e)];
    if (e.here && e.place) words.push("вы остановились на " + clock(e.place[0]));
    else if (e.done) words.push("досмотрена");
    if (e.new) words.push("новая");
    words.push(e.disk.length ? "в папке" : e.site.length ? "на сайте, онлайн" : "нет ни в папке, ни на сайте");
    const dl = dlOf(e);
    if (dl) words.push(dl.words);
    return words.join(", ");
  }
  function tile(e) {
    const a = el("a", "sv-tile vep");
    a.dataset.ep = e.key;
    const top = el("span", "sv-tile-top");
    const mark = el("span", "sv-tile-mark");
    mark.setAttribute("aria-hidden", "true");
    top.append(el("span", "sv-tile-n", e.label), mark);
    const bar = el("span", "sv-tile-bar");
    bar.setAttribute("aria-hidden", "true");
    a.append(top, el("span", "sv-tile-sub"), bar);
    a.addEventListener("click", (event) => {
      if (!playable(e)) { event.preventDefault(); return; }
      if (navigates() || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      choose(e.key, { autoplay: true });
      toPlayer();
    });
    paintTile(a, e);
    return a;
  }
  function paintTile(a, e) {
    const dl = dlOf(e);
    const cls = ["sv-tile", "vep", e.disk.length ? "disk" : e.site.length ? "site" : "off"];
    if (e.done) cls.push("done");
    if (e.here) cls.push("here");
    if (e.new) cls.push("new");
    if (current && e.key === current) cls.push("now");
    a.className = cls.join(" ");
    if (dl) a.dataset.dl = dl.state; else delete a.dataset.dl;
    a.style.setProperty("--dl", dl ? Math.round(100 * dl.part) + "%" : "0%");
    const mark = a.querySelector(".sv-tile-mark");
    mark.textContent = e.done ? "✓" : e.new ? "●" : "";
    // (v5.44.158) an episode watched to the end just now: its tick pops in («Плавные переходы», the mockup «Анимации»)
    const html = document.documentElement;
    if (e.done && wasDone.get(e.key) === false && html.dataset.motion === "on" && mark.animate) {
      const style = getComputedStyle(html);
      mark.animate([{ transform: "scale(0.2)", opacity: 0 }, { transform: "none", opacity: 1 }], {
        duration: parseFloat(style.getPropertyValue("--m-dialog")) || 190,
        easing: style.getPropertyValue("--m-ease").trim() || "ease-out",
      });
    }
    wasDone.set(e.key, !!e.done);
    let sub = "";
    if (dl) sub = dl.sub;
    else if (e.here && e.place) sub = "▶ " + clock(e.place[0]);
    else if (e.done) sub = "досмотрена";
    else if (e.new) sub = "новая";
    else if (e.disk.length) sub = "в папке";
    a.querySelector(".sv-tile-sub").textContent = sub;
    const bar = a.querySelector(".sv-tile-bar");
    const watched = e.place && e.place[1] && !e.done && !dl ? Math.min(100, Math.round(100 * e.place[0] / e.place[1])) : 0;
    bar.style.width = watched + "%";
    bar.hidden = !watched;
    if (playable(e)) { a.href = address(e.key, true); a.removeAttribute("aria-disabled"); }
    else { a.removeAttribute("href"); a.setAttribute("aria-disabled", "true"); }
    if (current && e.key === current) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
    const words = tileWords(e);
    a.title = words;
    a.setAttribute("aria-label", words);
  }
  function markCurrent() {               // the episode in the player framed (a long series: its hundred shown)
    const shown = current && [...page.querySelectorAll("[data-sv-eps] .sv-tile[data-ep]")].some((a) => a.dataset.ep === current);
    if (current && !shown && parts(episodes().length).length > 1) { hundred = -1; list(); return; }
    paintTiles();
  }
  function paintTiles() {                // the tiles as they are now (no new ones: the hover and the focus stay)
    page.querySelectorAll("[data-sv-eps] .sv-tile[data-ep]").forEach((a) => {
      const e = entryOf(a.dataset.ep);
      if (e) paintTile(a, e);
    });
    paintGoing();
  }
  function toPlayer() {                  // a tile chosen in a browser: the player under the tiles in sight
    const box = $("[data-sv-player]");
    if (!box || phone) return;
    const r = box.getBoundingClientRect();
    if (r.top > window.innerHeight * 0.55 || r.bottom < 80) box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function listHead() {
    const c = M.counts || {};
    const head = el("div", "sv-list-head");
    const h = el("h2", "", "Серии ");
    h.id = "sv-eps-h";
    h.append(el("span", "sv-count", String(c.total || 0)));
    head.append(h);
    if (!phone) head.append(legend());
    const tools = el("div", "sv-list-tools");
    if ((M.voices || []).length > 1 || (M.voices || []).some((v) => v.site.length)) {
      // the voice-over of the series — one choice for every episode (one of them in another: «Озвучка» of the player)
      const pick = el("div", "sv-voice-pick");
      if (!phone) pick.append(el("span", "sv-voice-label", "Смотреть в озвучке"));
      const now = currentVoice();
      const button = el("button", "sv-voice-now");
      button.type = "button";
      button.dataset.svVoice = "series";
      button.setAttribute("aria-haspopup", "dialog");
      button.setAttribute("aria-label", "Озвучка сериала: " + (now ? now.name : "как в файлах серий"));
      button.title = "Озвучка всего сериала — так идут и следующие серии; у одной серии другую можно выбрать в плеере";
      if (phone) button.append(icon("voice", 14));
      button.append(el("b", "", now ? now.name : "как в файлах серий"), icon("chev", 13));
      button.addEventListener("click", () => openVoices(null, button));
      pick.append(button);
      tools.append(pick);
    }
    if (episodes().length > 1 && !phone) {
      const order = el("button", "sv-link sv-order", newestFirst ? "↓ сначала новые" : "↑ по порядку");
      order.type = "button";
      order.title = "Порядок плиток";
      order.addEventListener("click", () => {
        newestFirst = !newestFirst;
        try { localStorage.setItem("lb-sv-order", newestFirst ? "new" : "old"); } catch (_) { /* this page */ }
        list();
      });
      tools.append(order);
    }
    if (tools.children.length) head.append(tools);
    return head;
  }
  function legend() {                    // what the tiles say: in the folder, only on the site, watched, new
    const c = M.counts || {};
    const ul = el("ul", "sv-legend");
    const item = (kind, text) => {
      const li = el("li");
      const swatch = el("span", "sv-sw " + kind, kind === "done" ? "✓" : "");
      swatch.setAttribute("aria-hidden", "true");
      li.append(swatch, text);
      ul.append(li);
    };
    if (M.series) item("disk", "в папке — " + (c.disk || 0));
    if (c.site) item("site", (M.series ? "только на сайте — " : "на сайте — ") + c.site);
    item("done", "досмотрено — " + (c.done || 0));
    if (c.new) item("new", (c.new === 1 ? "новая — " : "новые — ") + c.new);
    return ul;
  }

  function list() {
    const holder = $("[data-sv-list]");
    if (!holder) return;
    if (M.single) { holder.hidden = true; return; }
    holder.hidden = false;
    let eps = holder.querySelector("[data-sv-eps]");
    if (!eps) {
      eps = el("div", "sv-eps");
      eps.dataset.svEps = "";
      holder.prepend(eps);
    }
    let said = holder.querySelector("[data-sv-said]");
    if (!said) {                         // the card's own word (a download begun, stopped, not begun)
      said = el("p", "sv-said");
      said.dataset.svSaid = "";
      said.setAttribute("role", "status");
      said.hidden = true;
      eps.after(said);
    }
    const src = eps.querySelector("[data-sv-src]") || el("section", "sv-src");
    src.dataset.svSrc = "";
    src.setAttribute("aria-label", "Откуда серии");
    eps.textContent = "";
    eps.hidden = !!(picker && !phone);
    eps.append(listHead());
    const box = el("div", "sv-going");
    box.dataset.svGoing = "";
    box.setAttribute("role", "status");
    box.hidden = true;
    eps.append(box);
    const all = newestFirst ? episodes().slice().reverse() : episodes();
    const cut = parts(all.length);
    if (hundred < 0 || hundred >= cut.length) {   // the hundred of the episode in the player (or «Продолжить»)
      const key = current || (M.resume || {}).key || "";
      const at = Math.max(0, all.findIndex((e) => e.key === key));
      hundred = Math.max(0, cut.findIndex(([a, b]) => at >= a && at <= b));
    }
    if (cut.length > 1) {
      eps.append(hundreds(all.map((e) => e.label), hundred, (n) => { hundred = n; list(); }));
    }
    const grid = el("div", "sv-tiles vepisodes");
    // (v5.44.158) the tiles come one after another (booru.css) when they are new to the reader: the page opened, the
    // other hundred or order chosen, the site's episodes read.  Drawn again with the same tiles (a download's news,
    // an episode watched) the list stands — or goes on coming from where it was, if it has not finished
    const showing = [hundred, newestFirst ? "new" : "old", all.length ? "some" : "none"].join("|");
    if (showing !== tilesShown) {
      const first = tilesShown === null;
      tilesShown = showing;
      tilesAt = performance.now();
      if (first) {                           // the server's tiles began to come when the page was first drawn
        try {
          const painted = performance.getEntriesByType("paint")[0];
          if (painted) tilesAt = painted.startTime;
        } catch (_) { /* no such news in this browser: from now */ }
      }
    }
    const since = Math.round(performance.now() - tilesAt);
    if (since > 1500) grid.dataset.still = "";
    else if (since > 0) grid.style.setProperty("--m-since", since + "ms");
    let group = null;
    const [from, to] = cut[hundred];
    all.slice(from, to + 1).forEach((e) => {
      if (e.group !== group && (group !== null || e.group)) {    // a season's sub-folder: its name over its tiles
        grid.append(el("p", "sv-tiles-group", e.group || "Серии"));
      }
      group = e.group;
      grid.append(tile(e));
    });
    eps.append(grid);
    if (!all.length) {
      eps.append(el("p", "sv-hint", M.site && M.site.pending ? "Читаю серии с " + siteName() + "…"
        : "Серий пока нет" + (M.site && M.site.can && !M.site.online ? " — включите «Смотреть онлайн» ниже" : "")));
    } else if (phone) {
      eps.append(legend());
    } else {
      eps.append(el("p", "sv-hint", "Нажмите серию — она откроется в плеере ниже. Озвучка выбрана для всего сериала; " +
        "у одной серии другую можно выбрать в плеере."));
    }
    eps.append(src);
    paintGoing();
  }

  // what downloads for the series: the line over the tiles («Скачивается в папку: серия 9 · AniLibria — 62%», «Пауза»,
  // «Остановить», «Загрузки»), or its place in the queue
  function minutesWords(seconds) {
    const m = Math.max(1, Math.round(Number(seconds) / 60));
    return m >= 60 ? Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "") : m + " мин";
  }
  function releasesWords(groups) {       // «AniLibria, Dream Cast, субтитры Crunchyroll»
    return (groups || []).filter(Boolean).map((g) => (kindOf(g) === "subs" ? "субтитры " : kindOf(g) === "dub" ? "дубляж " : "")
      + shortName(g)).join(", ");
  }
  function qualityWords(q) {
    return !q ? "" : q === "best" ? "лучшее качество" : "не выше " + q + "p";
  }
  function paintGoing() {
    const box = $("[data-sv-going]");
    if (!box) return;
    const g = going && going.going;
    const q = going && going.queued;
    box.textContent = "";
    box.hidden = !g && !q;
    box.classList.toggle("queued", !g && !!q);
    if (!g && !q) return;
    box.append(icon("down", 18));
    const text = el("span", "sv-going-text");
    const head = el("b");
    const more = el("small");
    if (g) {
      const now = g.now;
      const pct = now ? Math.round(100 * (Number(now.part) || 0)) : 0;
      head.textContent = g.merging ? "Собираю озвучки серии в один файл…"
        : (g.paused ? "Пауза: " : "Скачивается в папку: ") + (now ? "серия " + now.label + (now.group ? " · " + shortName(now.group) : "")
          + " — " + pct + "%" : "готовлю файлы…");
      more.textContent = ["готово " + (g.ended || 0) + " из " + plural(g.count || 0, FILES),
        g.eta >= 60 ? "ещё около " + minutesWords(g.eta) : "", releasesWords(g.groups), qualityWords(g.quality),
        g.merge ? "в один файл" : ""].filter(Boolean).join(" · ");
    } else {
      head.textContent = "В очереди скачивания — " + (q.place > 1 ? q.place + "-м" : "следующим");
      more.textContent = [q.before ? "начнётся после «" + q.before + "»" : "начнётся само",
        q.files ? plural(q.files, FILES) : ""].filter(Boolean).join(" · ");
    }
    text.append(head, more);
    box.append(text);
    const buttons = el("span", "sv-going-buttons");
    const button = (words, data, fn, tip) => {
      const b = el("button", phone ? "m-button m-quiet" : "sv-btn small quiet", words);
      b.type = "button";
      b.dataset[data] = "";
      if (tip) b.title = tip;
      b.addEventListener("click", fn);
      buttons.append(b);
    };
    if (g) {
      button(g.paused ? "Продолжить" : "Пауза", "svPause", () => goingAct("/api/videos/download/pause", { pause: !g.paused }));
      button("Остановить", "svStop", () => goingAct("/api/videos/download/stop", {}, "Скачивание остановлено"),
             "Остановить скачивание (и очередь после него)");
    } else {
      button("Убрать из очереди", "svUnqueue", () => goingAct("/api/videos/download/unqueue", { id: q.id }, "Убрано из очереди"));
    }
    const link = el("a", "sv-link", "«Загрузки»");
    link.href = phone ? "/m/get/downloads" : "/get/downloads";
    link.title = "Все скачивания программы и их журнал";
    buttons.append(link);
    box.append(buttons);
  }
  async function goingAct(path, body, words) {
    try {
      await post(path, body);
      if (words) tell(words);
    } catch (err) {
      tell(err.message, true);
    }
    pollGoing();
  }
  let goingTimer = 0, goingOn = false, goingAsked = 0;
  async function pollGoing() {
    clearTimeout(goingTimer);
    if (!M.get || !M.get.url || M.single) { going = null; return; }
    const mine = ++goingAsked;
    let reply = null;
    try { reply = await getJson("/api/series/getting?" + query()); } catch (_) { reply = null; }
    if (mine !== goingAsked) return;
    const on = !!(reply && (reply.going || reply.queued));
    const was = goingOn;
    going = reply;
    goingOn = on;
    paintTiles();
    if (picker && picker.phase === "ready") paintGet();
    if (was && !on) gone(reply && reply.ended);
    goingTimer = setTimeout(pollGoing, on ? 1500 : document.hidden ? 30000 : 10000);
  }
  function gone(how) {                   // the download of the series ended: the tiles say what is in the folder now
    reload();
    if (!how) return;
    if (how.error) { tell(how.error, true); return; }
    const words = ["скачано " + plural(how.done || 0, FILES)];
    if (how.merged) words.push("собрано в один файл: " + plural(how.merged, EPISODES));
    if (how.failed) words.push("не скачались " + how.failed + " — почему, в «Загрузках»");
    tell((how.stopped ? "Скачивание остановлено: " : "Скачивание закончилось: ") + words.join(", "), !!how.failed);
  }

  // the variants of an episode («Озвучка» of the player): the computer — a list by its button; the phone — a sheet
  // at the bottom of the screen
  function variantItems(e) {
    const disk = e.disk.map((v) => Object.assign({}, v, { where: "disk" }));
    const site = e.site.map((n) => {
      const d = (M.dubs || [])[n] || {};
      return { kind: "site", name: d.short || d.name, sub: (d.kind === "subs" ? "субтитры" : d.kind === "dub" ? "дубляж" : "озвучка") + " · онлайн",
               dub: d.name, where: "site", key: d.key || "", voice: d.name };
    });
    return { disk, site };
  }
  function same(a, b) {
    return !!a && !!b && a.where === b.where && (a.where === "site" ? a.dub === b.dub
      : a.file === b.file && (a.kind === "file" || a.track === b.track));
  }
  function variantButton(e, v, tag) {
    const b = el(tag || "button", "sv-var" + (v.where === "site" ? " site" : ""));
    if (b.tagName === "BUTTON") b.type = "button";
    b.setAttribute("aria-pressed", same(v, e.chosen) ? "true" : "false");
    b.append(el("b", "", v.name), el("small", "", v.sub));
    b.addEventListener("click", () => pickVariant(e, v));
    return b;
  }
  // a sheet of choices at the bottom of the phone's screen (the voice-overs of an episode or of the series)
  function sheet(title, sub, groups) {
    const old = document.querySelector(".sv-sheet-wrap");
    if (old) old.remove();
    const wrap = el("div", "sv-sheet-wrap");
    const dim = el("div", "sv-sheet-dim");
    const box = el("section", "sv-sheet");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", title);
    box.append(el("span", "sv-sheet-handle"), el("h2", "", title));
    if (sub) box.append(el("p", "sv-sheet-sub", sub));
    groups.forEach(([label, iconName, items]) => {
      if (!items.length) return;
      const h = el("h3");
      h.append(icon(iconName, 13), " " + label);
      box.append(h);
      items.forEach((node) => box.append(node));
    });
    box.append(el("p", "sv-sheet-foot", "Выбор помнится для сериала: следующие серии пойдут в этой же озвучке."));
    const close = () => wrap.remove();
    dim.addEventListener("click", close);
    box.addEventListener("click", (event) => { if (event.target.closest(".sv-var")) setTimeout(close, 50); });
    wrap.append(dim, box);
    document.body.append(wrap);
    return wrap;
  }
  function openVoices(e, anchor) {
    if (e) {
      const { disk, site } = variantItems(e);
      if (phone) {
        sheet("Озвучка — " + e.name.toLowerCase(), "что есть у этой серии", [
          ["В папке — с диска", "folder", disk.map((v) => variantButton(e, v))],
          ["С сайта — онлайн, без скачивания", "globe", site.map((v) => variantButton(e, v))]]);
        return;
      }
      popover(anchor, [["В папке", disk.map((v) => variantButton(e, v))], ["С сайта, онлайн", site.map((v) => variantButton(e, v))]]);
      return;
    }
    const all = M.voices || [];
    const now = currentVoice();
    const item = (v) => {
      const b = el("button", "sv-var" + (v.disk.length ? "" : " site"));
      b.type = "button";
      b.setAttribute("aria-pressed", now && now.key === v.key ? "true" : "false");
      b.append(el("b", "", v.name), el("small", "", (v.sub || "") + " · " + voiceLine(v)));
      b.addEventListener("click", () => chooseVoice(v.voice || ""));
      return b;
    };
    const disk = all.filter((v) => v.disk.length).map(item);
    const site = all.filter((v) => !v.disk.length).map(item);
    if (phone) sheet("Озвучка сериала", "что есть у его серий", [["В папке — с диска", "folder", disk], ["С сайта — онлайн, без скачивания", "globe", site]]);
    else popover(anchor, [["В папке", disk], ["С сайта, онлайн", site]]);
  }
  function popover(anchor, groups) {
    const old = document.querySelector(".sv-pop");
    if (old) { old.remove(); if (old.anchorFor === anchor) return; }
    const pop = el("div", "sv-pop");
    pop.anchorFor = anchor;
    pop.setAttribute("role", "dialog");
    groups.forEach(([label, items]) => {
      if (!items.length) return;
      pop.append(el("p", "sv-pop-head", label));
      const g = el("div", "sv-pop-items");
      items.forEach((n) => g.append(n));
      pop.append(g);
    });
    anchor.insertAdjacentElement("afterend", pop);
    const close = () => {
      pop.remove();
      document.removeEventListener("click", away, true);
      document.removeEventListener("keydown", escape, true);
    };
    function away(event) {
      if (pop.contains(event.target) || event.target === anchor || anchor.contains(event.target)) return;
      close();
    }
    function escape(event) {                 // Escape closes it, the focus back on its button
      if (event.key !== "Escape") return;
      close();
      if (anchor.focus) anchor.focus();
    }
    setTimeout(() => document.addEventListener("click", away, true), 0);
    document.addEventListener("keydown", escape, true);
    pop.addEventListener("click", (event) => { if (event.target.closest(".sv-var")) setTimeout(close, 50); });
  }

  // ── «Скачать серии…» (v5.44.142): the choice of «Скачать» of the page of before on the tiles themselves ─────────
  // «Дубляж / Озвучка / Субтитры», several studios at once, «Качество», «Собрать озвучки серии в один файл», the weight;
  // what is downloaded is each episode ticked of each studio ticked, not what is in the folder already (or downloads
  // now).  The machinery of «Скачать с сайта»: /api/videos/find → find-state (one list at a time for the whole program),
  // /api/videos/measure, /api/videos/download.  The computer — the card «Серии» turns into it; the phone — a sheet.
  const KINDS = [["dub", "Дубляж"], ["voice", "Озвучка"], ["subs", "Субтитры"]];
  const isSubs = (name) => /^субтитры/i.test(name || "");
  const kindOf = (name) => (/^дубляж/i.test(name || "") ? "dub" : isSubs(name) ? "subs" : "voice");
  const shortName = (name) => String(name || "").replace(/^(озвучка|озучка|дубляж|субтитры)\s+/i, "") || String(name || "");
  // a voice-over named loosely (as the program's ``video_merge.dub_key``: «Озвучка X» — «X», no spaces nor signs)
  const looseKey = (name) => String(name || "").toLowerCase().replace(/^(озвучка|озучка)\s+/, "").replace(/[^\p{L}\p{N}]+/gu, "");
  const QUALITY = [["best", "лучшее, что есть"], ["1080", "не выше 1080p"], ["720", "не выше 720p"], ["480", "не выше 480p"],
                   ["360", "не выше 360p"]];
  const ENDED = ["done", "whole", "other", "failed", "dead"];
  const natural = (a, b) => String(a).localeCompare(String(b), "ru", { numeric: true });
  const stored = (key, fallback) => { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } };
  const store = (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* this time only */ } };
  let picker = null;                     // «Скачать серии…» open: {phase: finding | failed | ready, the list found, the choice}
  let shift = false;                     // a tile clicked with Shift: every one from the one before it

  function sameAddress(a, b) {           // one page of a site (no «www.», no «/» at the end — video_download._same_page)
    const key = (x) => {
      try {
        const u = new URL(String(x || ""));
        return u.hostname.replace(/^www\./, "") + u.pathname.replace(/\/+$/, "");
      } catch (_) { return ""; }
    };
    return !!key(a) && key(a) === key(b);
  }
  function folderName(path) {
    return String(path || "").split(/[\\/]+/).filter(Boolean).pop() || M.title || "";
  }
  function bytesWords(bytes) {
    const gb = bytes / 1024 ** 3;
    return gb >= 1 ? gb.toFixed(1).replace(".", ",") + " ГБ" : Math.max(1, Math.round(bytes / 1024 ** 2)) + " МБ";
  }

  function openGet() {
    if (!M.get || !M.get.url) return;
    if (picker) { focusGet(); return; }
    let quality = stored("lb.get-quality", "best");
    if (!QUALITY.some(([v]) => v === quality)) quality = "best";
    picker = { phase: "finding", said: "", error: "", find: 0, found: null, files: [], groups: [], kinds: [], labels: [],
               chosen: new Set(), episodes: new Set(), kind: "voice", quality, merge: M.get.merge !== false, dubs: false,
               measured: null, hundred: 0, last: "", busy: false, sounds: 0,
               // (v5.44.150) what the title's releases really give and which one the picture comes from
               // (/api/videos/measure: releases, picture, lower); «Заменить картинку у серий в папке»
               known: { releases: {}, picture: null, lower: { episodes: 0, height: 0 } }, again: false,
               asking: 0, askTimer: 0 };
    drawGet();
    focusGet();
    findGet(picker);
  }
  function closeGet() {
    picker = null;
    const wrap = document.querySelector(".sv-get-wrap");
    if (wrap) wrap.remove();
    const holder = $("[data-sv-list]");
    if (holder) {
      holder.classList.remove("getting");
      const box = holder.querySelector("[data-sv-get]");
      if (box) box.remove();
      const eps = holder.querySelector("[data-sv-eps]");
      if (eps) eps.hidden = false;
    }
    document.removeEventListener("keydown", getKeys, true);
  }
  function getKeys(event) {              // Escape closes it (not while a list of choices of the page is open)
    if (event.key !== "Escape" || document.querySelector(".sv-pop")) return;
    if (event.target && event.target.tagName === "SELECT") return;
    closeGet();
    const open = $("[data-sv-get-open]");
    if (open) open.focus();
  }
  function focusGet() {
    const box = getBox();
    if (!phone) {
      const holder = $("[data-sv-list]");
      if (holder) holder.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    const head = box.querySelector("#sv-get-h");
    if (head) head.focus({ preventScroll: true });
  }
  function getBox() {                    // where it is drawn: the card «Серии» (computer), a sheet (phone)
    if (phone) {
      let wrap = document.querySelector(".sv-get-wrap");
      if (!wrap) {
        wrap = el("div", "sv-sheet-wrap sv-get-wrap");
        const dim = el("div", "sv-sheet-dim");
        dim.addEventListener("click", closeGet);
        const sheet = el("section", "sv-sheet sv-get-sheet");
        sheet.setAttribute("role", "dialog");
        sheet.setAttribute("aria-labelledby", "sv-get-h");
        const handle = el("span", "sv-sheet-handle");
        handle.setAttribute("aria-hidden", "true");
        const box = el("div", "sv-get");
        box.dataset.svGet = "";
        sheet.append(handle, box);
        wrap.append(dim, sheet);
        document.body.append(wrap);
        document.addEventListener("keydown", getKeys, true);
      }
      return wrap.querySelector("[data-sv-get]");
    }
    const holder = $("[data-sv-list]");
    let box = holder.querySelector("[data-sv-get]");
    if (!box) {
      box = el("section", "sv-get");
      box.dataset.svGet = "";
      box.setAttribute("aria-labelledby", "sv-get-h");
      holder.append(box);
      document.addEventListener("keydown", getKeys, true);
    }
    holder.classList.add("getting");
    const eps = holder.querySelector("[data-sv-eps]");
    if (eps) eps.hidden = true;
    return box;
  }

  async function findGet(mine) {
    try {
      let state = null;
      try { state = await getJson("/api/videos/find-state"); } catch (_) { state = null; }
      // the list of this page found a moment ago (here or on «По ссылке»): as it is — the server looks at the folder anew
      const fresh = state && state.status === "done" && state.found && Number(state.age) < 600 &&
        sameAddress(state.address || state.found.page_url, M.get.url);
      if (!fresh) {
        const started = await post("/api/videos/find", { url: M.get.url });
        state = await waitFind(started.id, mine);
      }
      if (picker !== mine) return;
      takeFound(mine, state);
    } catch (err) {
      if (picker !== mine) return;
      mine.phase = "failed";
      mine.error = err.message;
      drawGet();
    }
  }
  async function waitFind(id, mine) {
    for (let after = 0; ;) {
      const state = await getJson("/api/videos/find-state?after=" + after);
      if (String(state.id) !== String(id)) {
        throw new Error("Поиск серий начала заново другая страница программы — нажмите «Скачать серии…» ещё раз");
      }
      after = Number(state.last_seq) || after;
      const lines = (state.log || []).map((x) => x.text).filter((t) => t && !/^(ГОТОВО|ОСТАНОВЛЕНО|СТОП)/.test(t));
      if (lines.length && mine && picker === mine) {
        mine.said = lines[lines.length - 1];
        const line = getBox().querySelector("[data-sv-get-said]");
        if (line) line.textContent = mine.said;
      }
      if (state.status === "done" && state.found) return state;
      if (state.status === "failed" || state.status === "stopped" || state.status === "done") {
        throw new Error(state.error || (state.status === "stopped" ? "Поиск серий остановлен" : "Серии для скачивания не нашлись"));
      }
      await sleep(600);
    }
  }
  function takeFound(p, state) {
    const f = state.found;
    p.phase = "ready";
    p.find = state.id;
    p.found = f;
    p.files = (f.files || []).filter((x) => x.kind !== "audio");
    p.sounds = (f.files || []).length - p.files.length;
    p.dubs = f.choose === "dubs";
    p.groups = [];
    p.files.forEach((x) => { if (!p.groups.includes(x.group || "")) p.groups.push(x.group || ""); });
    // chosen at first: the releases begun in the folder (as «По ссылке»), else the voice-over the series plays; the
    // files of a page — all its releases
    const begun = (g) => p.files.some((x) => (x.group || "") === g && (x.have === "whole" || x.have === "part" || x.have === "other"));
    let chosen = p.groups.filter(begun);
    if (!p.dubs) {
      chosen = p.groups.slice();
    } else if (!chosen.length) {
      const now = currentVoice();
      const want = [M.dub, now ? now.voice || now.name : ""].map(looseKey).filter(Boolean);
      chosen = p.groups.filter((g) => want.includes(looseKey(g))).slice(0, 1);
    }
    p.chosen = new Set(chosen);
    p.kinds = p.dubs ? KINDS.map(([k]) => k).filter((k) => p.groups.some((g) => kindOf(g) === k)) : [];
    p.kind = chosen.length && p.dubs ? kindOf(chosen[0]) : p.kinds.includes("voice") ? "voice" : p.kinds[0] || "voice";
    p.labels = [...new Set(p.files.map((x) => x.label || ""))].sort(natural);
    // ticked at first: the episodes the folder has not («которых нет в папке»)
    p.episodes = new Set(p.labels.filter(missingHere));
    p.measured = p.dubs ? null : {};
    drawGet();
    if (p.dubs) askPicture(p);
  }
  // (v5.44.150; the owner: «удалило весь видеоряд а оставило jam где всего 720p») the weight of an episode, and
  // what its picture will be: each release's real quality and cut, the release the picture comes from for the
  // voice-overs ticked (one not ticked lends its picture alone), the episodes of the folder with a lower one — asked
  // again when the ticks that decide it change (the releases are looked at once: quick after that)
  const voicesOf = (p) => [...p.chosen].filter((g) => !isSubs(g));
  function askPicture(p) {
    clearTimeout(p.askTimer);
    p.askTimer = setTimeout(() => {
      const mine = ++p.asking;
      const query = ["find=" + encodeURIComponent(p.find), "quality=" + encodeURIComponent(p.quality)]
        .concat(p.merge ? voicesOf(p).map((g) => "g=" + encodeURIComponent(g)) : []);
      getJson("/api/videos/measure?" + query.join("&")).then((reply) => {
        if (picker !== p || mine !== p.asking) return;
        p.measured = reply.measured || {};
        p.known = { releases: reply.releases || {}, picture: reply.picture || null,
                    lower: reply.lower || { episodes: 0, height: 0 } };
        paintGet();
      }).catch(() => { if (picker === p && mine === p.asking) { p.measured = p.measured || {}; paintGet(); } });
    }, p.measured === null ? 0 : 250);
  }
  // «Заменить картинку у N серий в папке»: how many (0 — not ticked, or there is none to replace)
  function againCount(p) {
    const lower = p.known.lower || {};
    const pic = p.known.picture;
    return p.dubs && p.merge && p.again && pic && pic.group && voicesOf(p).length ? Number(lower.episodes) || 0 : 0;
  }
  function missingHere(label) {
    const e = entryOf(label);
    return !e || !e.disk.length;
  }

  function drawGet() {
    const p = picker;
    if (!p) return;
    const box = getBox();
    box.textContent = "";
    const head = el("div", "sv-get-head");
    const folder = p.found ? folderName(p.found.folder) : (M.folder && M.folder.name) || M.title;
    const h = el("h2", "", phone ? "Скачать в папку" : "Скачать в папку «" + folder + "»");
    h.id = "sv-get-h";
    h.tabIndex = -1;
    head.append(h);
    if (!phone && p.phase === "ready" && p.dubs) {
      head.append(el("span", "sv-get-note", "скачается каждая отмеченная серия в каждой отмеченной озвучке"));
    }
    const close = el("button", "sv-get-close", phone ? "✕" : "Закрыть ✕");
    close.type = "button";
    close.dataset.svGetClose = "";
    close.setAttribute("aria-label", "Закрыть «Скачать серии»");
    close.addEventListener("click", closeGet);
    head.append(close);
    box.append(head);
    if (p.phase !== "ready") {
      const line = el("p", "sv-get-wait" + (p.phase === "failed" ? " bad" : ""));
      line.setAttribute("role", p.phase === "failed" ? "alert" : "status");
      if (p.phase === "failed") {
        line.append(el("span", "", p.error + " "));
        const again = el("button", "sv-link", "Спросить ещё раз");
        again.type = "button";
        again.addEventListener("click", () => {
          p.phase = "finding";
          p.error = "";
          drawGet();
          findGet(p);
        });
        line.append(again);
      } else {
        const spin = el("span", "sv-spin");
        spin.setAttribute("aria-hidden", "true");
        const words = el("span", "", p.said || "Ищу серии на " + (M.get.site || "сайте") + "…");
        words.dataset.svGetSaid = "";
        line.append(spin, words);
      }
      box.append(line);
      return;
    }
    // the releases: the kind, its studios, «Качество», «в один файл»
    const top = el("div", "sv-get-top");
    const main = el("div", "sv-get-main");
    if (p.kinds.length > 1) {
      const tabs = el("div", "sv-kinds");
      tabs.setAttribute("role", "tablist");
      tabs.setAttribute("aria-label", "Вид");
      p.kinds.forEach((k) => {
        const tab = el("button", "sv-kind");
        tab.type = "button";
        tab.setAttribute("role", "tab");
        tab.dataset.kind = k;
        tab.append(el("span", "", KINDS.find(([x]) => x === k)[1]), el("span", "sv-kind-n"));
        tab.addEventListener("click", () => { p.kind = k; drawGet(); const t = getBox().querySelector(`.sv-kind[data-kind="${k}"]`); if (t) t.focus(); });
        tabs.append(tab);
      });
      main.append(tabs);
    }
    const mine = p.groups.filter((g) => !p.dubs || kindOf(g) === p.kind);
    if (p.dubs || p.groups.length > 1) {
      const studios = el("ul", "sv-studios");
      studios.setAttribute("aria-label", p.dubs ? KINDS.find(([x]) => x === p.kind)[1] : "Релизы");
      mine.forEach((g) => studios.append(studioRow(p, g)));
      main.append(studios);
      if (mine.length > 1) {
        const tools = el("p", "sv-studio-tools");
        const all = el("button", "sv-link", p.dubs ? "отметить все этого вида" : "отметить все");
        all.type = "button";
        all.addEventListener("click", () => { mine.forEach((g) => p.chosen.add(g)); paintGet(); });
        const none = el("button", "sv-link", "снять все");
        none.type = "button";
        none.addEventListener("click", () => { mine.forEach((g) => p.chosen.delete(g)); paintGet(); });
        tools.append(all, none);
        main.append(tools);
      }
    }
    if (p.dubs && p.kind === "subs") {
      main.append(el("p", "sv-get-note", "Слова субтитров вшиты в картинку: каждый релиз — отдельное видео, весит как серия " +
        "и в общий файл не собирается."));
    }
    if (main.children.length) top.append(main);
    if (p.dubs) {
      const side = el("div", "sv-get-side");
      const quality = el("label", "sv-get-quality");
      const select = el("select");
      select.dataset.svGetQuality = "";
      QUALITY.forEach(([value, words]) => {
        const option = el("option", "", words);
        option.value = value;
        option.selected = value === p.quality;
        select.append(option);
      });
      select.addEventListener("change", () => {
        p.quality = select.value;
        store("lb.get-quality", select.value);
        askPicture(p);
        paintGet();
      });
      quality.append(el("span", "", "Качество"), select);
      const merge = el("label", "sv-get-merge");
      const tick = el("input");
      tick.type = "checkbox";
      tick.checked = p.merge;
      tick.dataset.svGetMerge = "";
      tick.addEventListener("change", () => { p.merge = tick.checked; askPicture(p); paintGet(); });
      const words = el("span");
      words.append(el("b", "", phone ? "Озвучки серии — в один файл" : "Собрать озвучки серии в один файл"));
      if (!phone) {
        words.append(el("small", "", "картинка серии — одна, лучшая у тайтла; остальные озвучки качаются только ради " +
          "звука и ложатся дорожками в тот же файл"));
      }
      merge.append(tick, words);
      // (v5.44.150) which release the picture comes from, and the episodes of the folder with a lower one
      const pic = el("p", "sv-get-note sv-get-picture");
      pic.dataset.svGetPicture = "";
      pic.hidden = true;
      const again = el("label", "sv-get-merge sv-get-again");
      again.hidden = true;
      const againTick = el("input");
      againTick.type = "checkbox";
      againTick.checked = p.again;
      againTick.dataset.svGetAgain = "";
      againTick.addEventListener("change", () => { p.again = againTick.checked; paintGet(); });
      again.append(againTick, el("span", "", ""));
      side.append(quality, merge, pic, again);
      if (!phone) {
        side.append(el("p", "sv-get-note", "Плееры: Kodik — до 720p, CVH — до 1080p, AniLiberty — до 1080p. Программа " +
          "берёт лучший в пределах качества."));
      }
      top.append(side);
    }
    if (top.children.length) box.append(top);
    // what the site says of its files
    notes(p).forEach(([text, warn, go]) => {
      const line = el("p", "sv-get-note" + (warn ? " warn" : ""), text);
      if (go) {                          // (v5.44.152) «Смотреть на сайте»: the window opens it in the usual browser
        const link = el("a", "sv-get-go", go.text);
        link.href = go.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        line.append(" ", link);
      }
      box.append(line);
    });
    // the episodes
    const eh = el("div", "sv-get-eps-head");
    eh.append(el("h3", "", "Серии"));
    const counted = el("span", "sv-get-counted");
    counted.dataset.svGetCounted = "";
    eh.append(counted);
    const quick = el("span", "sv-get-quick");
    quick.setAttribute("role", "group");
    quick.setAttribute("aria-label", "Отметить серии");
    if (!phone) quick.append(el("span", "sv-muted", "Отметить:"));
    [["new", phone ? "нет в папке" : "которых нет в папке"], ["all", "все"], ["none", "ничего"]].forEach(([how, words]) => {
      const b = el("button", "sv-link", words);
      b.type = "button";
      b.dataset.svPick = how;
      b.addEventListener("click", () => pickEpisodes(how));
      quick.append(b);
    });
    eh.append(quick);
    if (!phone) eh.append(el("span", "sv-muted sv-get-shift", "Shift+щелчок — подряд"));
    box.append(eh);
    const cut = parts(p.labels.length);
    p.hundred = Math.min(p.hundred, cut.length - 1);
    if (cut.length > 1) box.append(hundreds(p.labels, p.hundred, (n) => { p.hundred = n; drawGet(); }));
    const grid = el("div", "sv-pick-grid");
    grid.dataset.svGetGrid = "";
    const [from, to] = cut[p.hundred];
    p.labels.slice(from, to + 1).forEach((label) => grid.append(pickTile(p, label)));
    box.append(grid);
    if (!phone && p.dubs) {
      const legendList = el("ul", "sv-get-legend");
      [["on", "отмечена"], ["", "«есть» — у выбранных озвучек уже в папке"], ["", "«1/3» — из трёх выбранных одна уже есть"],
        ["", "«нет» — у выбранных этой серии нет"]].forEach(([cls, words]) => {
        const li = el("li");
        if (cls) { const sw = el("span", "sv-sw pick"); sw.setAttribute("aria-hidden", "true"); li.append(sw); }
        li.append(words);
        legendList.append(li);
      });
      box.append(legendList);
    }
    // «Скачать N файлов», what it takes on the disk, where it goes
    const foot = el("div", "sv-get-foot");
    const go = el("button", phone ? "m-button sv-get-go" : "sv-btn primary sv-get-go");
    go.type = "button";
    go.dataset.svGetGo = "";
    go.append(icon("down", 16), el("span", "", ""));
    go.addEventListener("click", getGo);
    const sum = el("span", "sv-get-sum");
    sum.dataset.svGetSum = "";
    foot.append(go, sum);
    box.append(foot);
    paintGet();
  }
  function notes(p) {                    // [words, a warning, a link after them] of what the find said of the files
    const out = [];
    const f = p.found || {};
    (f.sources || []).forEach((x) => {
      if (!x.exact) out.push([(x.dub || "Серии — с " + x.site) + ": " + x.what + " — " + x.how + ".", true]);
      (x.borrowed || []).forEach((other) => out.push(["Не этого тайтла: " + x.site + " приложил к странице видео тайтла «" +
        other.title + "» — их здесь нет, чтобы под этим именем не скачались чужие серии.", true]));
    });
    // (v5.44.152) the voice-overs only in players the program takes nothing from: by player — why, where to watch
    const page = /^https?:\/\//.test(f.page_url || "") ? { href: f.page_url, text: "Смотреть на сайте" } : null;
    if (p.dubs) (f.closed || []).forEach((c) => out.push(["Только в плеере " + c.player + ": " + c.names.join(", ") +
      ". " + c.why + ".", false, page]));
    const blocked = p.files.find((x) => x.blocked);
    if (blocked) out.push(["Часть файлов программа не скачивает: " + blocked.blocked + ".", false]);
    if (p.sounds) out.push(["На странице есть и файлы звука (" + p.sounds + ") — их можно скачать в «Скачать → По ссылке».", false]);
    return out;
  }
  function studioRow(p, g) {
    const files = p.files.filter((x) => (x.group || "") === g);
    const li = el("li");
    const label = el("label", "sv-studio");
    const tick = el("input");
    tick.type = "checkbox";
    tick.value = g;
    tick.addEventListener("change", () => {
      if (tick.checked) p.chosen.add(g); else p.chosen.delete(g);
      if (p.dubs) askPicture(p);         // (v5.44.150) another release ticked: the picture may be another's
      paintGet();
    });
    const text = el("span", "sv-studio-text");
    text.append(el("b", "", g ? shortName(g) : M.get.site || "Серии сайта"));
    const facts = el("small", "", studioFacts(p, g));
    facts.dataset.svStudioFacts = g;
    text.append(facts);
    const had = files.filter((x) => x.have === "whole" || x.have === "other").map((x) => x.label || "");
    if (had.length) text.append(el("small", "sv-studio-have", "в папке: " + (had.length === 1 ? "серия " : "серии ") + ranges(had)));
    label.append(tick, text);
    if (g) label.title = g;
    li.append(label);
    return li;
  }
  // a release in words: its quality (v5.44.150: the real one once its files were looked at — CVH's «до 1080p» was the
  // player's best, AniLibria's files there end at 720p — and whether its cut is the original's), its players, its
  // episodes
  function studioFacts(p, g) {
    const files = p.files.filter((x) => (x.group || "") === g);
    let best = 0;
    const players = [];
    files.forEach((x) => {
      best = Math.max(best, parseInt(String(x.quality || "").replace(/\D+/g, ""), 10) || 0);
      if (p.dubs) {
        String(x.note || "").replace(/^Плеер:\s*/, "").split(", ").forEach((n) => { if (n && !players.includes(n)) players.push(n); });
      }
    });
    const labels = files.map((x) => x.label || "");
    const count = labels.length === p.labels.length ? plural(labels.length, EPISODES)
      : (labels.length === 1 ? "серия " : "серии ") + ranges(labels);
    const real = p.known.releases[g];
    const size = real && real.known ? real.height + "p" : best ? "до " + best + "p" : "";
    const cut = !real || real.clean === null || real.clean === undefined ? ""
      : real.clean ? "без перемонтажа" : "перемонтаж студии";
    return [size, cut, players.join(", "), count].filter(Boolean).join(" · ");
  }
  function pickTile(p, label) {
    const cell = el("label", "sv-pt");
    cell.dataset.label = label;
    const tick = el("input", "sv-vh");
    tick.type = "checkbox";
    tick.value = label;
    const top = el("span", "sv-pt-top");
    const mark = el("span", "sv-pt-tick");
    mark.setAttribute("aria-hidden", "true");
    top.append(el("span", "sv-pt-n", label), mark);
    cell.append(tick, top, el("span", "sv-pt-sub"));
    cell.addEventListener("click", (event) => { if (event.target !== tick) shift = event.shiftKey; });
    tick.addEventListener("click", (event) => { if (event.shiftKey) shift = true; });
    tick.addEventListener("change", () => {
      const on = tick.checked;
      if (shift && p.last && p.last !== label && p.labels.includes(p.last)) {
        const a = p.labels.indexOf(p.last), b = p.labels.indexOf(label);
        p.labels.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((x) => { if (on) p.episodes.add(x); else p.episodes.delete(x); });
      } else if (on) {
        p.episodes.add(label);
      } else {
        p.episodes.delete(label);
      }
      p.last = label;
      shift = false;
      paintGet();
    });
    return cell;
  }
  function pickEpisodes(how) {
    const p = picker;
    if (!p) return;
    p.episodes = new Set(how === "all" ? p.labels : how === "none" ? [] : p.labels.filter(missingHere));
    paintGet();
  }
  // the files of the series downloading now (or waiting in the queue): not asked again
  function goingNow() {
    const g = going && going.going;
    return new Set(((g && g.pairs) || []).filter((x) => !ENDED.includes(x.state)).map((x) => x.label + "\u0000" + x.group));
  }
  function episodeOf(p, label, busy) {   // the files of an episode in the studios chosen: had, downloading, the rest
    const files = p.files.filter((x) => (x.label || "") === label && p.chosen.has(x.group || ""));
    const had = files.filter((x) => x.have === "whole" || x.have === "other").length;
    const now = files.filter((x) => x.have !== "whole" && x.have !== "other" && busy.has(label + "\u0000" + (x.group || ""))).length;
    const shut = files.filter((x) => x.blocked && x.have !== "whole" && x.have !== "other" &&
      !busy.has(label + "\u0000" + (x.group || ""))).length;
    return { files: files.length, had, now, shut, left: files.length - had - now - shut };
  }
  function picked() {
    const p = picker;
    if (!p) return [];
    const busy = goingNow();
    return p.files.filter((x) => p.episodes.has(x.label || "") && p.chosen.has(x.group || "") && x.have !== "whole" &&
      x.have !== "other" && !x.blocked && !busy.has((x.label || "") + "\u0000" + (x.group || ""))).map((x) => x.i);
  }
  // what the chosen ones take on the disk: one episode measured in each quality (/api/videos/measure) — as «По ссылке»
  // counts it (get-link.js weight); the files of a page — the sizes the site said
  function weight(files) {
    const p = picker;
    let approx = false;
    if (!p.dubs) {
      let total = 0;
      files.forEach((i) => { const x = p.found.files[i]; total += Number(x.size) || 0; approx = approx || !!x.stream; });
      return [total, approx];
    }
    const m = p.measured;
    const again = againCount(p);
    if (!m || !m.sizes || !m.seconds || (!files.length && !again)) return [0, true];
    const heights = Object.keys(m.sizes).map(Number).sort((a, b) => b - a);
    const cap = p.quality === "best" ? 1e9 : Number(p.quality) || 1e9;
    const sizeAt = (limit) => {
      const h = heights.find((x) => x <= Math.min(cap, limit)) || heights[heights.length - 1];
      return m.sizes[String(h)] || 0;
    };
    const limitOf = (x) => {
      const real = p.known.releases[x.group || ""];
      return real && real.known ? real.height : /CVH/.test(x.note || "") ? 1e9 : /AniLiberty/.test(x.note || "") ? 1080 : 720;
    };
    const sound = 14000 * m.seconds;
    const lent = p.merge && p.known.picture && p.known.picture.lent ? p.known.picture : null;
    let total = again * sizeAt(p.known.picture ? p.known.picture.height : 1e9);
    const byEpisode = new Map();
    files.forEach((i) => {
      const x = p.found.files[i];
      if (!byEpisode.has(x.label)) byEpisode.set(x.label, []);
      byEpisode.get(x.label).push(x);
    });
    byEpisode.forEach((mine, label) => {
      const voices = mine.filter((x) => !isSubs(x.group));
      total += mine.filter((x) => isSubs(x.group)).reduce((s, x) => s + sizeAt(limitOf(x)), 0);
      if (!voices.length) return;
      if (!p.merge) { total += voices.reduce((s, x) => s + sizeAt(limitOf(x)), 0); return; }
      const had = p.files.some((x) => x.label === label && !isSubs(x.group) && x.have === "whole");
      // a picture lent by a release not ticked: its picture and every ticked one's sound
      if (lent && !had) { total += sizeAt(lent.height) + voices.length * sound; return; }
      total += (had ? 0 : sizeAt(Math.max(...voices.map(limitOf)))) + (voices.length - (had ? 0 : 1)) * sound;
    });
    return [total, true];
  }
  function paintGet() {
    const p = picker;
    if (!p || p.phase !== "ready") return;
    const box = getBox();
    box.querySelectorAll(".sv-kind").forEach((tab) => {
      const mine = p.groups.filter((g) => kindOf(g) === tab.dataset.kind);
      const on = mine.filter((g) => p.chosen.has(g)).length;
      tab.setAttribute("aria-selected", tab.dataset.kind === p.kind ? "true" : "false");
      tab.querySelector(".sv-kind-n").textContent = on ? on + "/" + mine.length : String(mine.length);
    });
    box.querySelectorAll(".sv-studio").forEach((label) => {
      const tick = label.querySelector("input");
      tick.checked = p.chosen.has(tick.value);
      label.classList.toggle("on", tick.checked);
      const facts = label.querySelector("[data-sv-studio-facts]");
      if (facts) facts.textContent = studioFacts(p, tick.value);
    });
    // (v5.44.150) the picture of the episodes: whose, how large, of which cut; the folder's lower ones
    const picNote = box.querySelector("[data-sv-get-picture]");
    const againTick = box.querySelector("[data-sv-get-again]");
    if (picNote && againTick) {
      const pic = p.known.picture;
      const on = !!(p.merge && pic && pic.group && voicesOf(p).length);
      picNote.hidden = !on;
      if (on) {
        const cut = pic.clean === true ? ", без перемонтажа" : pic.clean === false ? ", перемонтаж студии" : "";
        picNote.textContent = "Картинка серий — от «" + pic.title + "»: " + pic.height + "p" + cut
          + (pic.lent ? ". Её озвучка не отмечена — скачается только ради картинки, её звук в файл серии не пойдёт." : ".");
      }
      const lower = p.known.lower || {};
      const can = on && lower.episodes > 0;
      againTick.closest("label").hidden = !can;
      if (!can) { p.again = false; againTick.checked = false; }
      else {
        againTick.closest("label").querySelector("span").textContent = "Заменить картинку у "
          + plural(lower.episodes, ["серии", "серий", "серий"]) + " в папке: сейчас до " + lower.height + "p, станет "
          + pic.height + "p";
      }
    }
    const busy = goingNow();
    const some = p.chosen.size > 0;
    let ticked = 0;
    p.labels.forEach((label) => {
      const s = episodeOf(p, label, busy);
      if (s.left > 0 && p.episodes.has(label)) ticked += 1;
    });
    box.querySelectorAll(".sv-pt").forEach((cell) => {
      const label = cell.dataset.label;
      const s = episodeOf(p, label, busy);
      const tick = cell.querySelector("input");
      tick.disabled = s.left <= 0;
      tick.checked = !tick.disabled && p.episodes.has(label);
      cell.classList.toggle("on", tick.checked);
      cell.classList.toggle("off", tick.disabled);
      cell.querySelector(".sv-pt-tick").textContent = tick.checked ? "✓" : "";
      const sub = !some ? "" : !s.files ? "нет" : s.left > 0 ? (s.had ? s.had + "/" + s.files : "")
        : s.now ? "качается" : s.had ? "есть" : "нельзя";
      cell.querySelector(".sv-pt-sub").textContent = sub;
      const said = { "нет": "у выбранных её нет", "есть": "уже в папке", "качается": "скачивается сейчас",
                     "нельзя": "её файлы не скачать" }[sub] || (sub ? "в папке " + s.had + " из " + s.files : "");
      tick.setAttribute("aria-label", "Серия " + label + (said ? ", " + said : ""));
      cell.title = "Серия " + label + (said ? " — " + said : "");
    });
    const counted = box.querySelector("[data-sv-get-counted]");
    if (counted) {
      counted.textContent = some ? "отмечено " + ticked + " из " + p.labels.length
        + (p.dubs ? " · выбрано: " + plural(p.chosen.size, ["релиз", "релиза", "релизов"]) : "")
        : p.dubs ? "выберите озвучку — серии покажут, что у неё есть" : "";
    }
    const files = picked();
    const again = againCount(p);       // (v5.44.150) nothing ticked, «Заменить картинку…» is: the pictures alone
    const go = box.querySelector("[data-sv-get-go]");
    const later = !!(going && (going.going || going.other));
    if (go) {
      go.disabled = (!files.length && !again) || p.busy;
      go.querySelector("span").textContent = p.busy ? "Начинаю…" : files.length
        ? (later ? "В очередь: " : "Скачать ") + plural(files.length, FILES)
        : again ? (later ? "В очередь: " : "") + "Заменить картинку у " + plural(again, ["серии", "серий", "серий"])
          : some || !p.dubs ? "Отметьте серии" : "Выберите озвучку";
    }
    const sum = box.querySelector("[data-sv-get-sum]");
    if (sum) {
      const [bytes, approx] = weight(files);
      const words = [];
      if ((files.length || again) && bytes) words.push((approx ? "≈ " : "") + bytesWords(bytes) + " на диске");
      words.push("в папку «" + folderName(p.found.folder) + "» — " + (p.found.new_folder ? "новая" : "уже есть, новые серии лягут в неё"));
      if (later) words.push(going.going ? "начнётся после текущего скачивания" : "начнётся после «" + going.other + "»");
      if (!phone) words.push("ход — на плитках и в «Скачать → Загрузки»");
      sum.textContent = words.join(" · ");
      sum.classList.remove("bad");
    }
  }
  async function getGo() {
    const p = picker;
    if (!p || p.busy) return;
    let files = picked();
    const again = againCount(p) > 0;
    if (!files.length && !again) return;
    p.busy = true;
    paintGet();
    const body = () => ({ find: p.find, files, quality: p.dubs ? p.quality : "", merge: !!(p.dubs && p.merge),
                          repicture: again });
    try {
      let reply;
      try {
        reply = await post("/api/videos/download", body());
      } catch (err) {
        if (!/устарел/i.test(err.message)) throw err;
        // the list is another page's now (one list at a time for the program): found again, the same episodes of the
        // same releases chosen
        const wanted = new Set(files.map((i) => { const x = p.found.files[i]; return (x.label || "") + "\u0000" + (x.group || ""); }));
        const started = await post("/api/videos/find", { url: M.get.url });
        const state = await waitFind(started.id, null);
        p.find = state.id;
        p.found = state.found;
        p.files = (state.found.files || []).filter((x) => x.kind !== "audio");
        files = p.files.filter((x) => wanted.has((x.label || "") + "\u0000" + (x.group || "")) && x.have !== "whole" &&
          x.have !== "other" && !x.blocked).map((x) => x.i);
        if (!files.length && !again) throw new Error("Эти серии уже в папке");
        reply = await post("/api/videos/download", body());
      }
      closeGet();
      tell(reply.queued ? "В очереди скачивания" + (reply.queued > 1 ? " " + reply.queued + "-м" : " следующим")
        + " — начнётся само; его видно на плитках" : "Скачивание началось — его видно на плитках и в «Загрузках»");
      pollGoing();
    } catch (err) {
      if (picker === p) {                // said by the button too (the line over the card may be out of sight)
        p.busy = false;
        paintGet();
        const sum = getBox().querySelector("[data-sv-get-sum]");
        if (sum) { sum.textContent = err.message; sum.classList.add("bad"); }
      }
      tell(err.message, true);
    }
  }

  // ── the choices of the series ──────────────────────────────────────────────────────────────────────────────────
  async function chooseOnline(on) {
    try {
      const reply = await post("/api/series/choose", Object.assign(ref(), { online: !!on }));
      take(reply.model);
      if (on && M.site && M.site.pending) waitSite(false);
    } catch (err) {
      if (err.data && err.data.hidden && hiddenByMode()) return;
      say(err.message, true);
    }
  }
  async function chooseVoice(name) {
    try {
      const reply = await post("/api/series/choose", Object.assign(ref(), { voice: name }));
      take(reply.model);
    } catch (err) {
      say(err.message, true);
    }
  }
  async function pickVariant(e, v) {
    // the episode in this variant (it plays now, or the next time it is chosen); the series' voice-over from now on
    const asked = { kind: v.kind, file: v.file || "", track: v.track, dub: v.dub || "" };
    if (current === e.key && engine) {
      if (engine.kind === "native" && v.kind === "track" && engine.file === v.file && LB.nativeVideo &&
          LB.nativeVideo.audioTracks) {
        const tracks = LB.nativeVideo.audioTracks();
        if (tracks.length > v.track || !tracks.length) {
          // the same file: playing — its track switched, the play goes on; not begun — kept, it begins in it
          if (tracks.length) LB.nativeVideo.chooseAudio(v.track);
          engine.variant = Object.assign({}, v, { where: "disk" });
          playerHead();                                // «Озвучка» says it at once (the series keeps it meanwhile)
          await chooseVoice(v.voice || v.name);
          return;
        }
      }
      if (navigates()) {
        await post("/api/series/choose", Object.assign(ref(), { voice: v.voice || "" })).catch(() => {});
        location.href = address(e.key, true);
        return;
      }
      await choose(e.key, { autoplay: !video || !video.paused, at: position, variant: asked });
      return;
    }
    await chooseVoice(v.voice || "");
  }
  function take(model) {
    if (!model) return;
    M = model;
    // the site's episodes came: the player names the one «Продолжить» goes to (nothing is opened before ▶)
    if (!current && M.resume && M.resume.key && !(engine && engine.token)) {
      current = M.resume.key;
      if (nbox && nbox.dataset.stream === "" && !nbox.dataset.ep) nbox.dataset.ep = current;
    }
    render();
  }
  let waiting = 0;
  async function waitSite(again) {
    const mine = ++waiting;
    let first = true;
    for (;;) {
      let reply;
      try {
        reply = await getJson("/api/series?" + query({ wait: "1" }) + (again && first ? "&again=1" : ""));
      } catch (err) {
        if (err.data && err.data.hidden && hiddenByMode()) return;
        if (mine !== waiting) return;
        if (M.site) { M.site.pending = false; M.site.error = err.message; }
        sources();
        return;
      }
      first = false;
      if (mine !== waiting) return;
      take(reply.model);
      if (!M.site || !M.site.pending) return;
      await sleep(600);
    }
  }
  async function reload() {
    try { take((await getJson("/api/series?" + query())).model); } catch (_) { /* the page as it is */ }
  }

  // ── the player ─────────────────────────────────────────────────────────────────────────────────────────────────
  const nbox = $(".native-video");
  const video = $("[data-sv-video]");
  const play = $("[data-sv-play]");
  const screen = $("[data-sv-screen]");
  const bigButton = $("[data-sv-big]");
  const native = nbox ? LB.nativeVideo : null;
  let current = page.dataset.ep || "";      // the episode in the player (its key)
  let engine = null;                        // {kind: "native"|"video", ep, where, file, dub, skips, length, token}
  let position = 0, duration = 0, playing = false;
  let endedFor = "";                        // the play whose end was heard (once)
  let reported = 0;
  let opening = 0;
  let hls = null;
  let starting = false;
  // in the program's window an episode is a page of its own (the server chooses the player of its file); a browser and
  // the phone's player — the same <video> takes the next one; the phone's page of the series — its player's page
  const navigates = () => inApp || (phone && view !== "player");

  function problem(text, file) {
    const line = $("[data-sv-problem]");
    if (!line) { if (text) say(text, true); return; }
    line.textContent = text || "";
    line.hidden = !text;
    // a file of the folder the browser cannot show: the player Windows opens it in (the computer)
    if (text && file && !phone) {
      const open = el("button", "sv-btn", "Открыть в видеоплеере");
      open.type = "button";
      open.dataset.action = "vid-open";
      open.dataset.id = file;
      open.title = "Открыть файл в программе, которой Windows открывает видео";
      line.append(open);
    }
    if (text) {
      const slides = page.querySelector(".vslides");
      if (slides) slides.classList.add("gone");
    }
  }
  function waitLine(text, part) {
    const box = $("[data-sv-wait]");
    if (!box) return;
    box.hidden = !text;
    const words = box.querySelector("[data-sv-wait-text]");
    if (words) words.textContent = text || "";
    const bar = box.querySelector("[data-sv-wait-bar]");
    if (bar) bar.style.width = Math.round(100 * (part || 0)) + "%";
  }
  function stopAll() {
    if (hls) { hls.destroy(); hls = null; }
    if (video && !inApp) {
      video.pause();
      video.removeAttribute("src");
      try { video.load(); } catch (_) { /* nothing loaded */ }
    }
    if (native && nbox && nbox.dataset.stream) native.stop();
    playing = false;
  }

  // what the player shows of the episode: its name, from where, its variants; the strip of numbers; the buttons under
  function playerHead() {
    const holder = $("[data-sv-head]");
    if (!holder) return;
    const e = entryOf(current);
    holder.textContent = "";
    if (!e) {
      holder.append(el(phone ? "h2" : "h2", "sv-ep-name", M.single ? M.title : "Выберите серию"));
      return;
    }
    // the play of this episode — not the one before it, while the next one is asked of the program
    const mine = engine && engine.ep === e.key ? engine : null;
    const v = (mine && mine.variant) || e.chosen || {};
    const h = el("h2", "sv-ep-name", M.single ? M.title : nameOf(e));
    holder.append(h);
    const from = el("span", "sv-from " + (v.where === "site" ? "site" : "disk"));
    from.append(icon(v.where === "site" ? "globe" : "folder", 12), v.where === "site" ? "с " + siteName() + ", без скачивания" : "из папки");
    holder.append(from);
    if (mine && mine.where === "site" && (mine.player_name || mine.quality)) {   // whose stream, how good
      holder.append(el("span", "sv-stream", [mine.player_name, mine.quality].filter(Boolean).join(" · ")));
    }
    if (!phone && !M.single) holder.append(el("span", "sv-series", M.title + " · " + plural((M.counts || {}).total || 0, EPISODES)));
    const picks = el("div", "sv-picks");
    const variants = e.disk.length + e.site.length;
    const voice = el("button", "sv-pick sv-pick-voice");
    voice.type = "button";
    voice.dataset.svVoice = "episode";
    voice.append(icon("voice", 14), el("span", "", "Озвучка"), el("b", "", v.name || "—"), icon("chev", 13));
    voice.disabled = variants < 2;
    voice.addEventListener("click", () => openVoices(e, voice));
    picks.append(voice);
    // an episode of the site: its player and quality (a title of a page — Hentasis — has neither: one file)
    if (v.where === "site" && (M.qualities || []).length) {
      const d = (M.dubs || []).find((x) => x.name === v.dub) || {};
      const players = d.players || [];
      const picked = M.picks || {};
      if (players.length > 1) {
        picks.append(selectPick("Плеер", "player", [["", "Авто"]].concat(players.map((p) => [p.key, p.name])),
                                picked.player || ""));
      }
      picks.append(selectPick("Качество", "quality", M.qualities.map((q) => [q === "best" ? "" : q,
                                                                             q === "best" ? "Лучшее" : q + "p"]),
                              picked.quality === "best" ? "" : picked.quality || ""));
    }
    if (engine && engine.kind === "video" && (engine.subs || []).length) {
      const subs = el("label", "sv-pick sv-pick-subs");
      subs.append(el("span", "", "Субтитры"));
      const select = el("select");
      select.dataset.svSubs = "";
      [["", "нет"]].concat(engine.subs.map((s) => [s.key, s.label])).forEach(([key, text]) => {
        const o = el("option", "", text);
        o.value = key;
        o.selected = key === (engine.sub || "");
        select.append(o);
      });
      select.addEventListener("change", () => showSubs(select.value, true));
      subs.append(select);
      picks.append(subs);
    }
    holder.append(picks);
  }
  function selectPick(label, field, options, now) {
    const pick = el("label", "sv-pick sv-pick-" + field);
    pick.append(el("span", "", label));
    const select = el("select");
    select.dataset.svPick = field;
    options.forEach(([value, text]) => {
      const o = el("option", "", text);
      o.value = value;
      o.selected = value === now;
      select.append(o);
    });
    select.addEventListener("change", () => choosePick(field, select.value));
    pick.append(select);
    return pick;
  }
  // «Плеер» / «Качество» chosen: kept for the series; the episode playing is opened again in them, where it is
  async function choosePick(field, value) {
    try {
      const reply = await post("/api/series/choose", Object.assign(ref(), { [field]: value }));
      M.picks = (reply.model && reply.model.picks) || Object.assign({}, M.picks, { [field]: value });
    } catch (err) {
      say(err.message, true);
      return;
    }
    if (!current || !engine || engine.where !== "site") return;
    if (navigates()) { location.href = address(current, true); return; }
    choose(current, { autoplay: !!video && !video.paused, at: position });
  }
  // the phone's page of an episode: the episodes under its player, six a row (the computer — the tiles of «Серии» over
  // the player, list())
  function strip() {
    const holder = $("[data-sv-strip]");
    if (!holder || M.single) { if (holder) holder.hidden = true; return; }
    holder.textContent = "";
    const head = el("div", "sv-eps-head");
    head.append(el("h3", "", "Серии"));
    const legend = el("span", "", "✓ досмотрена");
    if ((M.counts || {}).site) legend.append(" · ", el("span", "sv-site-word", "• с сайта"));
    head.append(legend);
    holder.append(head);
    const box = el("div", "sv-eps-grid");
    box.setAttribute("role", "tablist");
    box.setAttribute("aria-label", "Серии");
    episodes().forEach((e) => {
      const b = el("a", "sv-ep" + (e.key === current ? " now" : e.done ? " done" : !e.disk.length ? " site" : ""), e.label);
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", e.key === current ? "true" : "false");
      b.href = address(e.key, true);
      b.dataset.ep = e.key;
      b.title = nameOf(e) + (e.done ? ", досмотрена" : "") + (!e.disk.length ? ", с сайта" : "");
      if (e.key === current) b.setAttribute("aria-current", "true");
      if (!playable(e)) { b.removeAttribute("href"); b.classList.add("off"); }
      b.addEventListener("click", (event) => {
        if (!playable(e) || navigates()) { if (!playable(e)) event.preventDefault(); return; }
        event.preventDefault();
        choose(e.key, { autoplay: true });
      });
      box.append(b);
    });
    holder.append(box);
  }
  function under() {
    const holder = $("[data-sv-under]");
    if (!holder) return;
    holder.textContent = "";
    if (M.single) {
      if (!phone && engine && engine.where === "disk") holder.append(openOutside());
      return;
    }
    const before = current ? neighbour(current, -1) : null;
    const after = current ? neighbour(current, 1) : null;
    const link = (target, words, rel, key) => {
      const a = el("a", phone ? "m-button" + (rel === "prev" ? " m-quiet" : "") : "sv-btn", words);
      if (target) {
        a.href = hrefOf(target);
        if (navigates()) { a.rel = rel; a.dataset.key = key; }     // ← → (booru.js) go there
        a.addEventListener("click", (event) => {
          if (navigates()) return;
          event.preventDefault();
          choose(target.key, { autoplay: true });
        });
      } else {
        a.setAttribute("aria-disabled", "true");
        a.classList.add("off");
      }
      a.dataset.sv = rel;
      return a;
    };
    const nav = el("div", "sv-nav");
    nav.append(link(before, before ? "‹ " + before.name : "‹ Предыдущая", "prev", "ArrowLeft"),
               link(after, after ? after.name + " ›" : "Следующая ›", "next", "ArrowRight"));
    holder.append(nav);
    const auto = el("label", phone ? "m-check sv-auto" : "sv-auto");
    const box = el("input");
    box.type = "checkbox";
    box.checked = M.autonext !== "off";
    box.dataset.svAutonext = "";
    box.addEventListener("change", () => {
      M.autonext = box.checked ? "on" : "off";
      document.body.dataset.autonext = M.autonext;
      post("/api/series/choose", Object.assign(ref(), { autonext: box.checked })).catch(() => {});
    });
    auto.append(box, phone ? " Следующая серия сама" : " следующая серия сама");
    holder.append(auto);
    if (!phone) {
      if (engine && engine.where === "disk") holder.append(openOutside());
      const keys = el("span", "sv-keys");
      keys.innerHTML = "<kbd>Пробел</kbd> пауза · <kbd>←</kbd><kbd>→</kbd> предыдущая / следующая серия · <kbd>F</kbd> во весь экран";
      holder.append(keys);
    }
  }
  function openOutside() {
    const b = el("button", "sv-btn quiet", "Открыть в видеоплеере");
    b.type = "button";
    b.dataset.action = "vid-open";
    b.dataset.id = (engine && engine.file) || "";
    b.title = "Открыть файл в программе, которой Windows открывает видео";
    return b;
  }

  // ── an episode in the player ──
  async function choose(key, opts) {
    opts = opts || {};
    const e = entryOf(key);
    if (!e || !playable(e)) return;
    if (navigates()) { location.href = address(key, !!opts.autoplay); return; }
    report(true);                                     // where the episode before stopped
    if (!opts.first) {                                // another episode chosen: the frames of the first one go
      const slides = page.querySelector(".vslides");
      if (slides) slides.classList.add("gone");
    }
    const asked = ++opening;
    current = e.key;
    endCard(false);
    problem("");
    waitLine("");
    if (!opts.first || opts.autoplay) {               // the address names the episode playing (a reload goes on)
      try { history.replaceState(history.state, "", address(e.key, true).replace(/#player$/, "")); } catch (_) { /* the address */ }
    }
    renderPlayer();
    markCurrent();
    let reply;
    try {
      reply = await post("/api/series/play", Object.assign(ref(), { ep: e.key, phone, variant: opts.variant || null,
                                                                    player: (M.picks || {}).player || "", quality: (M.picks || {}).quality || "" }));
    } catch (err) {
      if (asked !== opening) return;
      if (err.data && err.data.hidden && hiddenByMode()) return;
      problem(e.name + " не открылась: " + err.message);
      return;
    }
    if (asked !== opening) return;
    if (opts.variant) reload();                     // the variant asked is the series' voice-over now: the list says it
    const at = opts.at !== undefined && opts.at !== null ? Number(opts.at) || 0 : Number(reply.from) || 0;
    engine = { kind: "video", ep: e.key, where: reply.where, variant: reply.variant, file: reply.episode || "", dub: reply.dub || "",
               skips: reply.skips || {}, length: Number(reply.length) || 0, token: reply.token || "", subs: [], sub: "",
               player_name: reply.player_name || "", quality: reply.quality || "" };
    endedFor = "";
    position = 0;
    duration = engine.length;
    reported = Date.now();
    renderPlayer();
    if (reply.kind === "stream") {
      engine.subs = [];
      loadStream(reply, at, !!opts.autoplay);
    } else {
      const ph = reply.phone || {};
      engine.subs = ph.subs || reply.subs || [];
      engine.sub = ph.sub || reply.sub || "";
      engine.audio = ph.chosen;
      playerHead();
      if (ph.error && !ph.src && !ph.direct) { problem(ph.error); return; }
      if (ph.src) loadFile(ph.src, at, !!opts.autoplay);
      else if (reply.phone) prepare(reply.episode, ph.chosen || 0, at, !!opts.autoplay, asked);
      else loadFile(reply.src, at, !!opts.autoplay);
    }
  }
  function prepare(episode, audio, at, go, asked) {
    waitLine("Готовлю серию для телефона…", 0);
    post("/api/phone/video/prepare", { episode, audio }).then((state) => {
      if (asked !== opening) return;
      if (state.src && (state.state === "ready" || state.state === "direct")) { waitLine(""); loadFile(state.src, at, go); return; }
      poll(episode, audio, at, go, asked);
    }, (err) => { if (asked === opening) { waitLine(""); problem(err.message); } });
  }
  function poll(episode, audio, at, go, asked) {
    fetch("/api/phone/video/state?episode=" + episode + "&audio=" + audio, { credentials: "same-origin" })
      .then((r) => r.json()).then((state) => {
        if (asked !== opening) return;
        if (state.state === "ready") { waitLine(""); loadFile(state.src, at, go); return; }
        if (state.state === "failed") { waitLine(""); problem(state.error || "Серия не собралась"); return; }
        const part = Number(state.part) || 0;
        waitLine(state.state === "waiting" ? "Серия ждёт очереди — готовится другая…" : "Готовлю серию… " + Math.round(100 * part) + "%", part);
        setTimeout(() => poll(episode, audio, at, go, asked), 800);
      }).catch(() => {
        if (asked !== opening) return;
        waitLine("Нет связи с программой — пробую снова…", 0);
        setTimeout(() => poll(episode, audio, at, go, asked), 2500);
      });
  }
  function begin(at, go) {
    const jump = () => { if (at > 1 && (!video.duration || at < video.duration - 1)) { try { video.currentTime = at; } catch (_) { /* from the start */ } } };
    if (video.readyState >= 1) jump(); else video.addEventListener("loadedmetadata", jump, { once: true });
    if (go) video.play().catch(() => { if (bigButton) bigButton.hidden = false; });
  }
  function loadFile(src, at, go) {
    if (!video) return;
    if (hls) { hls.destroy(); hls = null; }
    Array.from(video.querySelectorAll("track")).forEach((t) => t.remove());
    (engine.subs || []).forEach((s) => {
      const t = document.createElement("track");
      t.kind = "subtitles";
      t.src = s.src;
      t.label = s.label;
      t.dataset.key = s.key;
      video.append(t);
    });
    video.preload = "auto";
    if (engine.where === "disk" && engine.file) video.poster = "/videos/frame/" + engine.file;
    video.src = src;
    showSubs(engine.sub || "", false);
    begin(at, go);
    marksDraw();
  }
  function loadStream(info, at, go) {
    if (!video) return;
    if (hls) { hls.destroy(); hls = null; }
    video.removeAttribute("poster");
    video.preload = "auto";
    if (info.stream === "hls" && window.Hls && window.Hls.isSupported()) {
      let retried = false;
      hls = new window.Hls({ enableWorker: false, startPosition: at > 1 ? at : -1, maxBufferLength: 45, backBufferLength: 60 });
      hls.on(window.Hls.Events.ERROR, (_event, data) => {
        if (!data || !data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR && !retried) { retried = true; hls.startLoad(); return; }
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && !retried) { retried = true; hls.recoverMediaError(); return; }
        problem("Видео не показывается: " + (data.details || "ошибка потока") + ". Попробуйте другую озвучку.");
      });
      hls.loadSource(info.src);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => { if (go) video.play().catch(() => { if (bigButton) bigButton.hidden = false; }); });
    } else if (info.stream === "mp4" || video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = info.src;
      begin(at, go);
    } else {
      problem("Этот браузер не показывает потоки HLS — откройте страницу в окне программы или в другом браузере");
    }
    marksDraw();
  }
  function showSubs(key, keep) {
    if (!video) return;
    const tracks = Array.from(video.querySelectorAll("track"));
    Array.from(video.textTracks || []).forEach((t, n) => {
      const node = tracks[n];
      t.mode = node && key && node.dataset.key === key ? "showing" : "disabled";
    });
    if (keep && engine && engine.file && (!key || key.charAt(0) === "x")) {
      post("/api/videos/tracks", { episode: engine.file, value: key || "-1" }).catch(() => {});
    }
    if (engine) engine.sub = key;
  }

  // the window's own player: an episode of the site — its stream opened by the page, then played
  async function openNativeStream(go) {
    if (!nbox || nbox.dataset.stream || starting) return;
    const e = entryOf(nbox.dataset.ep || current);
    if (!e) return;
    starting = true;
    try {
      const reply = await post("/api/series/play", Object.assign(ref(), { ep: e.key, variant: nbox.dataset.dub
        ? { kind: "site", dub: nbox.dataset.dub } : null, player: (M.picks || {}).player || "", quality: (M.picks || {}).quality || "" }));
      if (reply.kind !== "stream") { location.href = address(e.key, true); return; }
      engine = { kind: "native", ep: e.key, where: "site", variant: reply.variant, dub: reply.dub, skips: reply.skips || {},
                 length: Number(reply.length) || 0, token: reply.token, player_name: reply.player_name || "",
                 quality: reply.quality || "" };
      nbox.dataset.stream = reply.token;
      nbox.dataset.from = String(Number(nbox.dataset.from) || Number(reply.from) || 0);
      if (reply.length) nbox.dataset.length = String(reply.length);
      renderPlayer();
      marksDraw();
      if (go && native) native.start();
    } catch (err) {
      if (err.data && err.data.hidden && hiddenByMode()) return;
      const p = nbox.querySelector(".nv-problem");
      if (p) { p.querySelector("p").textContent = e.name + " не открылась: " + err.message; p.hidden = false; }
      else say(err.message, true);
    } finally {
      starting = false;
    }
  }

  // ⏮ ⏭ and «Серия 5 из 12» on the bar (the window's own player: put into its bar; the page's: its own)
  function barNumbers() {
    const e = entryOf(current);
    const list = episodes();
    const at = e ? list.indexOf(e) + 1 : 0;
    const before = e ? neighbour(e.key, -1) : null;
    const after = e ? neighbour(e.key, 1) : null;
    const said = e && !M.single ? e.name + " из " + list.length : "";
    if (nbox) {
      const bar = nbox.querySelector(".nv-bar");
      if (!bar) return;
      let lead = bar.querySelector(".sv-lead");
      if (!lead) {
        lead = el("span", "sv-lead");
        const playButton = bar.querySelector(".nv-play");
        if (playButton) playButton.insertAdjacentElement("afterend", lead); else bar.prepend(lead);
      }
      lead.textContent = "";
      if (M.single) return;
      const prev = el("a", "nv-prev sv-b");
      prev.title = before ? "Предыдущая: " + before.name : "Предыдущей нет";
      prev.setAttribute("aria-label", "Предыдущая серия");
      prev.innerHTML = '<svg class="sv-i" viewBox="0 0 24 24" aria-hidden="true"><path d="M19 5v14L9 12z"/><path d="M5 5v14"/></svg>';
      if (before) prev.href = hrefOf(before); else prev.classList.add("off");
      const next = el("a", "nv-next sv-b sv-next");
      next.title = after ? "Следующая: " + after.name : "Следующей нет";
      next.setAttribute("aria-label", "Следующая серия");
      next.innerHTML = '<svg class="sv-i" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5v14l10-7z"/><path d="M19 5v14"/></svg>';
      if (after) { next.href = hrefOf(after); next.append(el("span", "", after.name)); } else next.classList.add("off");
      lead.append(prev, next);
      if (said) lead.append(el("span", "sv-num", said.replace(/^Сезон.*· серия/, "Серия")));
      return;
    }
    const num = $("[data-sv-num]");
    if (num) { num.textContent = said; num.hidden = !said; }
    const prev = $("[data-sv-prev]"), next = $("[data-sv-next]");
    if (prev) { prev.disabled = !before; prev.title = before ? "Предыдущая: " + before.name : "Предыдущей нет"; }
    if (next) {
      next.disabled = !after;
      next.title = after ? "Следующая: " + after.name + " (→)" : "Следующей нет";
      const label = next.querySelector("[data-sv-next-label]");
      if (label) label.textContent = after && !phone ? after.name : "";
    }
    if (prev) prev.hidden = !!M.single;
    if (next) next.hidden = !!M.single;
  }
  if (play) {
    const prev = $("[data-sv-prev]"), next = $("[data-sv-next]");
    if (prev) prev.addEventListener("click", () => step(-1));
    if (next) next.addEventListener("click", () => step(1));
  }
  function step(n) {
    const e = current ? neighbour(current, n) : null;
    if (!e) return;
    const like = variantLike(e);
    if (navigates()) location.href = address(like && like.file ? like.file : e.key, true);
    else choose(e.key, { autoplay: true, variant: like });
  }

  // the opening and the ending marked on the bar; «Пропустить …» while it goes
  function skipsOf() {
    if (engine && engine.skips && Object.keys(engine.skips).length) return engine.skips;
    if (nbox && nbox.dataset.skips) { try { return JSON.parse(nbox.dataset.skips) || {}; } catch (_) { return {}; } }
    if (video && video.dataset.skips) { try { return JSON.parse(video.dataset.skips) || {}; } catch (_) { return {}; } }
    return {};
  }
  function marksDraw() {
    const skips = skipsOf();
    // the file's length its sound was measured with (the stretches' own clock), else the player's
    const length = Number(skips.length) || Number(duration) || (engine && engine.length) ||
      Number((nbox || video || {}).dataset ? (nbox || video).dataset.length : 0) || 0;
    let holder = null;
    if (nbox) {
      const seek = nbox.querySelector(".nv-seek");
      holder = nbox.querySelector(".nv-marks");
      if (seek && !holder) {
        const wrap = el("span", "nv-seekwrap");
        seek.parentNode.insertBefore(wrap, seek);
        wrap.appendChild(seek);
        holder = el("span", "nv-marks sv-marks");
        holder.setAttribute("aria-hidden", "true");
        wrap.appendChild(holder);
      }
    } else {
      holder = $("[data-sv-marks]");
    }
    if (!holder) return;
    holder.textContent = "";
    if (!(length > 0)) return;
    ["opening", "ending"].forEach((kind) => {
      const span = skips[kind];
      if (!Array.isArray(span) || !(span[1] > span[0])) return;
      const mark = el("span", "sv-mark-bar " + (kind === "opening" ? "op" : "ed"));
      mark.style.left = Math.max(0, Math.min(100, 100 * span[0] / length)).toFixed(2) + "%";
      mark.style.width = Math.max(0.4, Math.min(100, 100 * (span[1] - span[0]) / length)).toFixed(2) + "%";
      mark.title = (kind === "opening" ? "Опенинг " : "Эндинг ") + clock(span[0]) + "–" + clock(span[1]);
      mark.append(el("em", "", kind === "opening" ? "опенинг" : "эндинг"));
      holder.append(mark);
    });
  }
  function skipButtons() {
    const skips = skipsOf();
    // while the opening is on the screen (playing or paused in it); a play that ended or never began — none (the
    // window's own player: booru.js hides them when it stops)
    const on = nbox ? true : !!video && !video.ended && !!(video.currentSrc || hls);
    page.querySelectorAll(".sv-skip").forEach((button) => {
      const span = skips[button.dataset.skip];
      button.hidden = !(on && Array.isArray(span) && position >= span[0] && position < span[1] - 1);
    });
  }
  page.querySelectorAll(".sv-skip").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const span = skipsOf()[button.dataset.skip];
      if (!Array.isArray(span)) return;
      button.hidden = true;
      if (nbox && native) native.seekTo(span[1]);
      else if (video) video.currentTime = Math.min(span[1], (video.duration || span[1]) - 0.5);
    });
  });

  // where the episode stopped: every 10 s while it plays, on a pause, at its end, when the page goes
  function report(now, ended) {
    if (!current) return;
    if (!now && Date.now() - reported < 10000) return;
    if (!ended && position < 1) return;
    reported = Date.now();
    const skips = skipsOf();
    const ending = Array.isArray(skips.ending) ? Number(skips.ending[0]) || 0 : 0;
    const body = Object.assign(ref(), { ep: current, at: position, length: duration, ended: !!ended, ending,
                                        dub: engine && engine.where === "site" ? engine.dub : "" });
    post("/api/series/progress", body).catch(() => { /* asked again with the next moment */ });
  }
  window.addEventListener("pagehide", () => {
    if (!current || position < 1) return;
    const skips = skipsOf();
    const ending = Array.isArray(skips.ending) ? Number(skips.ending[0]) || 0 : 0;
    const body = JSON.stringify(Object.assign(ref(), { ep: current, at: position, length: duration, ended: false,
                                                       ending, dub: engine && engine.where === "site" ? engine.dub : "" }));
    try { navigator.sendBeacon("/api/series/progress", new Blob([body], { type: "application/json" })); } catch (_) { /* kept */ }
  });

  // the end of an episode: «Серия 8 закончилась → Следующая: серия 9» — after the folder's last one, the site's next
  let endTimer = 0;
  function endCard(on, fullSize) {
    const holder = nbox ? nbox.querySelector(".nv-screen") : screen;
    let card = page.querySelector(".sv-end");
    clearInterval(endTimer);
    if (!on) { if (card) card.remove(); return; }
    if (!holder) return;
    const e = entryOf(current);
    const after = e ? neighbour(e.key, 1) : null;
    if (card) card.remove();
    card = el("div", "sv-end");
    card.setAttribute("role", "status");
    const inside = el("div", "sv-end-card");
    inside.append(el("p", "sv-end-h", M.single || !e ? "Видео закончилось" : e.name + " закончилась"));
    if (e && !M.single) inside.append(el("p", "sv-end-was", (e.title ? "«" + e.title + "» — " : "") + "отмечена досмотренной"));
    if (after) {
      const go = el("a", "sv-end-next");
      go.href = hrefOf(after);
      go.append(el("span", "", "Следующая: " + after.name.toLowerCase()), icon("forth", 16));
      go.addEventListener("click", (event) => { event.preventDefault(); goTo(after, fullSize); });
      inside.append(go);
      const where = !after.disk.length ? "с " + siteName() + ", онлайн — в папке её нет" : "из папки";
      const what = el("p", "sv-end-what");
      if (after.title) what.append("«" + after.title + "» · ");
      what.append(el("span", after.disk.length ? "disk" : "site", where));
      inside.append(what);
      if (M.autonext !== "off") {
        let left = 5;
        const soon = el("p", "sv-end-soon");
        const count = el("b", "", left + " с");
        const stay = el("button", "sv-link", "Остаться");
        stay.type = "button";
        stay.addEventListener("click", () => { clearInterval(endTimer); soon.remove(); });
        soon.append("начнётся сама через ", count, " · ", stay);
        inside.append(soon);
        endTimer = setInterval(() => {
          left -= 1;
          count.textContent = left + " с";
          if (left <= 0) { clearInterval(endTimer); goTo(after, fullSize); }
        }, 1000);
      }
    } else if (e && !M.single) {
      inside.append(el("p", "sv-end-what", "Это последняя серия" + (M.site && !M.site.online && M.site.can ? " в папке — остальные есть на сайте: «Смотреть онлайн»" : "")));
    }
    const again = el("button", "sv-link sv-end-again", "Смотреть снова");
    again.type = "button";
    again.addEventListener("click", () => {
      endCard(false);
      if (nbox && native) { nbox.dataset.from = "0"; native.restart(); }
      else if (video) { video.currentTime = 0; video.play().catch(() => {}); }
    });
    inside.append(again);
    card.append(inside);
    holder.append(card);
  }
  // the episode after (before) this one goes on in the same variant: its sub-folder, its track, the site's voice-over
  function hrefOf(e) {
    const like = variantLike(e);
    return address(like && like.file ? like.file : e.key, true);
  }
  function variantLike(e) {
    const now = (engine && engine.variant) || {};
    if (!e || !now.key) return null;
    const v = e.disk.find((x) => x.key === now.key);
    if (v) return { kind: v.kind, file: v.file, track: v.track, dub: "" };
    const n = e.site.find((k) => ((M.dubs || [])[k] || {}).key === now.key);
    return n !== undefined ? { kind: "site", dub: M.dubs[n].name } : null;
  }
  function goTo(e, fullSize) {
    endCard(false);
    if (navigates()) {
      const like = variantLike(e);
      let target = address(like && like.file ? like.file : e.key, true);
      if (fullSize) target = target.replace("#player", "") + "&full=1#player";
      location.href = target;
      return;
    }
    choose(e.key, { autoplay: true, at: 0, variant: variantLike(e) });
  }
  function finished(fullSize) {
    const token = (engine && engine.token) || current;
    if (endedFor === token + "|" + current) return;
    endedFor = token + "|" + current;
    playing = false;
    report(true, true);
    const e = entryOf(current);
    if (e) { e.done = true; e.place = null; }
    if (fullSize) {                          // the window's own full-size player ended the episode: the next, full size
      const after = e ? neighbour(e.key, 1) : null;
      if (after && M.autonext !== "off") { goTo(after, true); return; }
    }
    endCard(true, fullSize);
    markCurrent();
    strip();
  }

  // the window's own player: its state
  if (nbox) {
    nbox.addEventListener("lb-video-state", (event) => {
      const st = event.detail || {};
      const was = playing;
      position = Number(st.position) || 0;
      duration = Number(st.duration) || duration;
      playing = !!st.playing;
      if (playing) endCard(false);
      // a voice-over switched in the player itself (its full-screen menu): «Озвучка» over it follows
      if (st.audio !== undefined && st.audio !== null && engine && engine.ep === current && engine.file) {
        const e = entryOf(current);
        const v = e && e.disk.find((x) => x.kind === "track" && x.file === engine.file && x.track === Number(st.audio));
        if (v && !(engine.variant && engine.variant.kind === "track" && engine.variant.track === v.track)) {
          engine.variant = Object.assign({}, v, { where: "disk" });
          playerHead();
        }
      }
      skipButtons();
      if (!playing && was && !st.ended) report(true);
      else report(false);
      if (st.ended) finished(false);
      if (duration && !marksDrawnFor) { marksDrawnFor = duration; marksDraw(); }
    });
    nbox.addEventListener("lb-video-full-left", (event) => {
      const d = event.detail || {};
      position = Number(d.position) || 0;
      duration = Number(d.duration) || duration;
      if (d.ended) finished(true);
    });
    // the big ▶ of an episode of the site before its stream is opened
    nbox.addEventListener("click", (event) => {
      if (nbox.dataset.stream !== "" || !event.target.closest("[data-action='nv-toggle']")) return;
      event.preventDefault();
      event.stopPropagation();
      openNativeStream(true);
    }, true);
  }
  let marksDrawnFor = 0;

  // the page's own <video> and its bar
  if (video && play) {
    const toggle = $("[data-sv-toggle]");
    const time = $("[data-sv-time]");
    const range = $("[data-sv-range]");
    const done = $("[data-sv-done]");
    const mute = $("[data-sv-mute]");
    const vol = $("[data-sv-vol]");
    let seeking = false;
    const paint = () => {
      const d = video.duration && isFinite(video.duration) ? video.duration : duration;
      if (time) time.textContent = clock(position) + " / " + clock(d);
      const part = d > 0 ? Math.min(1, position / d) : 0;
      if (range && !seeking) range.value = String(Math.round(1000 * part));
      if (done) done.style.width = (100 * part).toFixed(2) + "%";
      play.classList.toggle("playing", !video.paused);
      if (toggle) toggle.setAttribute("aria-label", video.paused ? "Пуск" : "Пауза");
    };
    video.addEventListener("timeupdate", () => {
      position = video.currentTime || 0;
      duration = video.duration && isFinite(video.duration) ? video.duration : duration;
      skipButtons();
      report(false);
      paint();
    });
    video.addEventListener("loadedmetadata", () => { duration = video.duration || duration; marksDraw(); paint(); });
    video.addEventListener("play", () => {
      playing = true;
      problem("");
      endCard(false);
      if (bigButton) bigButton.hidden = true;
      const slides = page.querySelector(".vslides");
      if (slides) slides.classList.add("gone");
      paint();
    });
    video.addEventListener("pause", () => { playing = false; if (!video.ended) report(true); paint(); });
    video.addEventListener("ended", () => { playing = false; paint(); finished(false); });
    video.addEventListener("error", () => {
      if (!video.getAttribute("src") || hls) return;
      if (video.dataset.other) return;                // the window: booru.js goes over to its own player
      if (bigButton) bigButton.hidden = true;
      problem("Этот файл браузер показать не может (формат не поддерживается).",
              engine && engine.where === "disk" ? engine.file : "");
    });
    if (toggle) toggle.addEventListener("click", () => togglePlay());
    if (bigButton) bigButton.addEventListener("click", () => togglePlay());
    screen.addEventListener("click", (event) => {
      if (event.target.closest("button, a, select, input, .sv-end")) return;
      togglePlay();
    });
    screen.addEventListener("dblclick", (event) => {
      if (event.target.closest("button, a, select, input")) return;
      fullScreen();
    });
    if (range) {
      range.addEventListener("input", () => {
        seeking = true;
        const d = video.duration || duration;
        if (time) time.textContent = clock(d * range.value / 1000) + " / " + clock(d);
      });
      range.addEventListener("change", () => {
        seeking = false;
        const d = video.duration || duration;
        if (d > 0) video.currentTime = d * range.value / 1000;
      });
    }
    if (mute) mute.addEventListener("click", () => { video.muted = !video.muted; mute.dataset.muted = video.muted ? "1" : "0"; });
    if (vol) vol.addEventListener("input", () => { video.volume = Number(vol.value) / 100; video.muted = false; mute.dataset.muted = "0"; });
    page.querySelectorAll("[data-sv-full]").forEach((b) => b.addEventListener("click", () => fullScreen()));
    paint();
  }
  function togglePlay() {
    if (!video) return;
    if (!video.getAttribute("src") && !hls) {
      const target = current ? entryOf(current) : null;
      const r = M.resume || {};
      const e = target && playable(target) ? target : entryOf(r.key);
      if (e) choose(e.key, { autoplay: true, at: e.key === r.key ? r.at : undefined });
      return;
    }
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  }
  function fullScreen() {
    if (!play) return;
    if (inApp) {                                 // the window: the page's player over the page, the window full screen
      const on = !play.classList.contains("sv-fullscreen");
      play.classList.toggle("sv-fullscreen", on);
      document.documentElement.classList.toggle("lb-video-full", on);
      if (LB.appRequest) LB.appRequest("video-screen", { on }).catch(() => {});
      return;
    }
    const full = document.fullscreenElement || document.webkitFullscreenElement;
    if (full) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); return; }
    const ask = play.requestFullscreen || play.webkitRequestFullscreen;
    if (!ask) return;
    Promise.resolve(ask.call(play)).then(() => {
      if (phone && screen.orientation && window.screen.orientation && window.screen.orientation.lock) {
        window.screen.orientation.lock("landscape").catch(() => { /* a phone that does not turn by itself */ });
      }
    }).catch(() => { /* not allowed here */ });
  }
  document.addEventListener("fullscreenchange", () => {
    if (play) play.classList.toggle("sv-whole", !!document.fullscreenElement);
  });
  document.addEventListener("keydown", (event) => {
    if (!video || !play || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.target;
    if (target && target.closest && target.closest("input:not([type=range]), textarea, select, [contenteditable]")) return;
    if (event.code === "Space" && !(target.closest && target.closest("button, a"))) {
      event.preventDefault();
      togglePlay();
    } else if (event.code === "KeyF") {
      event.preventDefault();
      fullScreen();
    } else if (event.code === "Escape" && play.classList.contains("sv-fullscreen")) {
      fullScreen();
    } else if ((event.code === "ArrowLeft" || event.code === "ArrowRight") && !navigates()) {
      if (target.closest && target.closest("input[type=range]")) return;
      event.preventDefault();
      step(event.code === "ArrowLeft" ? -1 : 1);
    }
  });

  function renderPlayer() {
    playerHead();
    strip();
    under();
    barNumbers();
  }
  function render() {
    hero();
    numbers();
    actions();
    list();
    sources();
    renderPlayer();
  }

  // ── the start ────────────────────────────────────────────────────────────────────────────────────────────────
  if (M.site && M.site.hidden && !M.series && hiddenByMode()) return;      // a title the mode hides (known already)
  render();
  if (M.site && M.site.pending) waitSite(false);
  pollGoing();                                   // (v5.44.142) what downloads for the series — on its tiles
  const asked = page.dataset.play === "1";
  // the variant the page plays: the file its address names (an address of before, the next one in the same variant)
  const playedVariant = (e) => {
    const file = page.dataset.file || "";
    const v = e && file ? e.disk.find((x) => x.file === file) : null;
    return v ? Object.assign({}, v, { where: "disk" }) : (e ? e.chosen : {});
  };
  if (nbox) {
    const e = entryOf(current);
    engine = { kind: "native", ep: current, where: nbox.dataset.stream !== undefined ? "site" : "disk", variant: playedVariant(e),
               file: nbox.dataset.episode || "", dub: nbox.dataset.dub || "", skips: {}, length: Number(nbox.dataset.length) || 0 };
    duration = engine.length;
    renderPlayer();
    marksDraw();
    if (nbox.dataset.stream === "" && nbox.dataset.ep && (asked || nbox.dataset.full === "1")) openNativeStream(true);
  } else if (video) {
    const e = entryOf(current);
    if (video.getAttribute("src")) {                // the window's browser plays this file (the server chose it)
      engine = { kind: "video", ep: current, where: "disk", variant: playedVariant(e), file: video.dataset.episode || "",
                 skips: {}, length: Number(video.dataset.length) || 0, subs: [], sub: "" };
      duration = engine.length;
      const at = Number(video.dataset.from) || 0;
      if (at > 1) begin(at, false);
      renderPlayer();
      marksDraw();
    } else {
      const v = page.dataset.asked === "1" ? playedVariant(e) : null;
      const variant = v && v.file ? { kind: v.kind, file: v.file, track: v.track, dub: "" } : null;
      if (e && (asked || view === "player")) {
        choose(e.key, { autoplay: asked, first: true, variant });
      } else if (e && e.disk.length) {
        choose(e.key, { autoplay: false, first: true, variant });   // its file at hand, waiting for ▶ (no stream)
      }
    }
  }
  page.dataset.ready = "1";
})();
