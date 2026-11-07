// Local Booru — «Смотреть»: a series of YummyAnime watched in the program without downloading it (v5.44.106;
// webui/pages/video_watch.py, core/video_watch.py; the look — the mockup «Сериал» the owner approved).
// The title comes from /api/watch/title (asked again while the site answers); the voice-over, the player and the
// quality are chosen above the episodes; an episode pressed — /api/watch/open gives the program's address of its video
// (a stream or a file — the program asks the site for it), played by the window's own player (booru.js nativeVideo:
// data-stream) or by <video> (hls.js where the browser does not play HLS itself).  Where it stopped —
// /api/watch/progress; «Скачать серию N» / «Скачать остальные» — the machinery of «Скачать» (/api/videos/find, then
// /api/videos/download: the other chat's — only asked).  (v5.44.109) A title of Hentasis too: its own files, one
// «player» and no «Качество» (their pickers are not drawn), its voice-overs — the sub-folders «Скачать» puts them in.
(() => {
  "use strict";
  const box = document.getElementById("watch-page");
  if (!box) return;
  const LB = window.LocalBooru || {};
  const url = box.dataset.url || "";
  const phone = box.dataset.phone === "1";
  const inApp = box.dataset.app === "1";
  const getPage = box.dataset.get || "/get";
  const siteName = box.dataset.site || "YummyAnime";
  const $ = (sel) => box.querySelector(sel);
  const PATHS = { chevron: "M6 9l6 6 6-6", play: "M8 5v14l11-7z", down: "M12 4v11M7 10.5l5 5 5-5M5 19.5h14",
                  star: "M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.4l-5.8 3.1 1.1-6.5L2.6 9.4l6.5-.9z",
                  check: "M5 12l5 5L20 7", out: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function icon(name, size, filled) {
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("width", String(size || 16));
    s.setAttribute("height", String(size || 16));
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("class", "wt-icon" + (filled ? " filled" : ""));
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", PATHS[name]);
    s.append(p);
    return s;
  }
  function plural(n, forms) {
    const a = Math.abs(n) % 100, b = a % 10;
    return n + " " + (a > 10 && a < 20 ? forms[2] : b > 1 && b < 5 ? forms[1] : b === 1 ? forms[0] : forms[2]);
  }
  function clock(value) {
    const t = Math.max(0, Math.floor(Number(value) || 0));
    const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0");
  }
  const score = (value) => Number(value).toFixed(1).replace(".", ",");
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

  // «← Лента» (and the phone's «назад»): back in the history to the feed (catalog.js marks its entry: the program
  // sends no Referer, the Navigation API hides the addresses) — (v5.44.107) wherever it is before this page (another
  // series opened from «Порядок просмотра»); none — the link, to the feed's last address, and the feed takes its list
  // back all the same (catalog.js: «lb-feed-return»)
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
        if (path === box.dataset.back) return entry;
      }
    } catch (_) { /* the link */ }
    return null;
  }
  function toFeed(link) {
    const path = box.dataset.back;
    let address = link.getAttribute("href") || path;
    try {
      const last = JSON.parse(sessionStorage.getItem("lb-feed-last") || "{}") || {};
      if (typeof last[path] === "string" && (last[path] === path || last[path].startsWith(path + "?"))) address = last[path];
      sessionStorage.setItem("lb-feed-return", JSON.stringify({ path, at: Date.now() }));
    } catch (_) { /* the feed as the server gives it */ }
    location.assign(address);
  }
  document.querySelectorAll("[data-wt-back], .m-top [data-back]").forEach((back) => {
    back.addEventListener("click", (event) => {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const entry = feedEntry();
      if (!entry) {
        toFeed(back);
        return;
      }
      try {
        const going = window.navigation.traverseTo(entry.key);
        if (going && going.committed) going.committed.catch(() => toFeed(back));
        if (going && going.finished) going.finished.catch(() => {});
      } catch (_) {
        toFeed(back);
      }
    });
  });

  function hiddenByMode() {
    const words = document.querySelector("template[data-wt-hidden]");
    if (!words) return false;
    stopPlayer();
    document.title = "Скрыто — Local Booru";
    box.replaceWith(words.content.cloneNode(true));
    return true;
  }

  // ── what is shown and chosen ──────────────────────────────────────────────────────────────────────────────────────
  let T = null;                    // the title (/api/watch/title)
  let dub = null;                  // the voice-over chosen (one of T.dubs)
  let player = "";                 // "" — the best within the quality (the program's order), else "kodik", "cvh"…
  let quality = "";                // "" — the best, else "1080", "720"…
  let current = "";                // the episode in the player (its label)
  let stream = null;               // what /api/watch/open gave for it
  let progress = {};
  let position = 0, duration = 0, playing = false;
  let endedFor = "";               // the token whose end was heard (once)
  let reported = 0;                // when the place was sent last
  let opening = 0;                 // the number of the last «open» asked (an older answer is dropped)

  function wait(text, bad) {
    const line = $("[data-wt-wait]");
    if (!line) return;
    line.textContent = text || "";
    line.hidden = !text;
    line.classList.toggle("bad", !!bad);
  }

  async function boot(again) {
    let first = true;
    for (;;) {
      let reply;
      try {
        reply = await getJson("/api/watch/title?url=" + encodeURIComponent(url) + (again && first ? "&again=1" : ""));
      } catch (err) {
        if (err.data && err.data.hidden && hiddenByMode()) return;
        return failed(err.message);
      }
      first = false;
      if (reply.title) return show(reply.title);
      if (reply.error) return failed(reply.error);
      wait((reply.said || "Читаю тайтл с " + siteName).replace(/…$/, "") + "…");
      await sleep(600);
    }
  }

  function failed(text) {
    wait(text, true);
    const line = $("[data-wt-wait]");
    const again = el("button", "wt-link", "Спросить ещё раз");
    again.type = "button";
    again.addEventListener("click", () => { wait("Спрашиваю ещё раз…"); boot(true); });
    line.append(" ", again);
  }

  function show(t) {
    T = t;
    progress = t.progress || {};
    const hero = $(".wt-hero");
    if (hero) hero.removeAttribute("aria-busy");
    wait("");
    heroOf(t);
    aboutOf(t);
    orderOf(t);
    dub = t.dubs.find((d) => d.name === progress.dub) ||
      t.dubs.slice().sort((a, b) => (b.have || 0) - (a.have || 0))[0];
    player = dub.players.some((p) => p.key === progress.player) ? progress.player : "";
    quality = (t.qualities || []).includes(progress.quality) && progress.quality !== "best" ? progress.quality : "";
    picks();
    tiles();
    actions();
    $("[data-wt-watch]").hidden = false;
    nav();
  }

  // ── the title ────────────────────────────────────────────────────────────────────────────────────────────────────
  function heroOf(t) {
    $("[data-wt-name]").textContent = t.title;
    document.title = t.title + " — Local Booru";
    const alt = $("[data-wt-alt]");
    alt.textContent = "";
    (t.alt || []).forEach((name) => alt.append(el("li", "", name)));
    alt.hidden = !(t.alt || []).length;
    const ratings = $("[data-wt-ratings]");
    if ((t.ratings || []).length) ratings.textContent = "";        // none named — the tile's stays
    (t.ratings || []).forEach((r, n) => {
      const chip = el(r.url ? "a" : "span", "wt-rate" + (n === 0 && r.name === t.site ? " wt-rate-own" : ""));
      if (r.url) {
        chip.href = r.url;
        chip.target = "_blank";
        chip.rel = "noopener noreferrer";
        chip.title = r.name;
      }
      if (n === 0 && r.name === t.site) {
        chip.append(icon("star", 18, true), el("b", "", score(r.value)));
        if (r.votes) chip.append(el("span", "wt-votes", plural(r.votes, ["голос", "голоса", "голосов"]).replace(/^(\d+)(\d{3}) /, "$1 $2 ")));
      } else {
        chip.append(el("span", "wt-rate-site", r.name), el("b", "", score(r.value)));
      }
      ratings.append(chip);
    });
    const facts = $("[data-wt-facts]");
    facts.textContent = "";
    const row = (label, value) => {
      facts.append(el("dt", "", label));
      const dd = el("dd");
      if (value instanceof Node) dd.append(value); else dd.textContent = value;
      facts.append(dd);
      return dd;
    };
    (t.facts || []).forEach(([label, value]) => {
      if (label === "Статус") {
        const dd = row(label, "");
        dd.append(el("span", "wt-status", value));
      } else if (label === "Серий") {
        const most = Math.max(0, ...t.dubs.map((d) => d.have || 0));
        const dd = row(label, value);
        if (most) dd.append(el("span", "wt-have", " · в папке " + most));
      } else {
        row(label, value);
      }
    });
    if ((t.genres || []).length) {
      const list = el("span", "wt-genres");
      t.genres.forEach((g) => list.append(el("span", "wt-chip", g)));
      row("Жанры", list);
    }
    const named = t.dubs.filter((d) => !d.plain);       // (a page's files the site names no one for — not a studio)
    if (named.length) {
      // a studio's subtitles and its voice-over are two releases: the kind is said where it is not a voice-over
      const names = named.map((d) => d.short + (d.kind === "subs" ? " (субтитры)" : d.kind === "dub" ? " (дубляж)" : ""));
      const dd = row("Озвучка", names.slice(0, 8).join(", "));
      if (names.length > 8) {
        const more = el("button", "wt-link", "ещё " + (names.length - 8));
        more.type = "button";
        more.addEventListener("click", () => { dd.textContent = names.join(", "); });
        dd.append(" ", more);
      }
    }
    facts.hidden = !facts.children.length;
    if (t.poster) {
      const holder = $("[data-wt-poster]");
      const old = holder.querySelector("img");
      const img = el("img");
      img.alt = "";
      img.decoding = "async";
      img.addEventListener("load", () => {
        img.classList.add("lb-in");               // motion.js hides a picture of <main> until it has «lb-in»
        if (old && old !== img) old.remove();
      }, { once: true });
      img.addEventListener("error", () => img.remove(), { once: true });
      holder.append(img);
      img.src = "/api/catalog/cover?c=" + encodeURIComponent(t.poster);
    }
  }

  function aboutOf(t) {
    const holder = $("[data-wt-about]");
    holder.textContent = "";
    const paragraphs = t.about || [];
    if (!paragraphs.length) return;
    const text = el("div", "wt-text");
    paragraphs.forEach((p, n) => { const node = el("p", "", p); if (n > 1) node.hidden = true; text.append(node); });
    holder.append(text);
    const line = el("div", "wt-about-line");
    if (paragraphs.length > 2) {
      const all = el("button", "wt-link", "Показать полностью");
      all.type = "button";
      all.addEventListener("click", () => { text.querySelectorAll("p").forEach((p) => { p.hidden = false; }); all.remove(); });
      line.append(all);
    }
    line.append(el("span", "wt-muted", "Описание с " + t.site));
    holder.append(line);
    holder.hidden = false;
  }

  function watchHref(address) {
    return (phone ? "/m" : "") + "/videos/watch?url=" + encodeURIComponent(address);
  }

  function orderOf(t) {
    const holder = $("[data-wt-order]");
    const order = t.order || [];
    holder.textContent = "";
    if (order.length < 2) return;
    const summary = el("summary");
    summary.append(el("span", "", "Порядок просмотра"), el("span", "wt-muted", plural(order.length, ["часть", "части", "частей"])));
    holder.append(summary);
    const list = el("ol", "wt-order-list");
    order.forEach((o) => {
      const li = el("li", o.here ? "here" : o.soon ? "soon" : "");
      const info = [o.type, o.year, o.kind, o.soon ? "анонс" : ""].filter(Boolean).join(" · ");
      if (o.here || o.soon || !o.address) {
        li.append(el("span", "wt-order-name", o.title + (o.here ? " — эта страница" : "")));
      } else {
        const link = el("a", "wt-order-name", o.title);
        link.href = watchHref(o.address);
        li.append(link);
      }
      if (info) li.append(el("span", "wt-muted", info));
      list.append(li);
    });
    holder.append(list);
    holder.hidden = false;
  }

  // ── «Продолжить», «Скачать…» ─────────────────────────────────────────────────────────────────────────────────────
  function watched(n) { return (progress.watched || []).includes(String(n)); }
  function placeOf(n) {
    const p = (progress.places || {})[String(n)];
    return Array.isArray(p) ? { at: Number(p[0]) || 0, length: Number(p[1]) || 0 } : null;
  }
  // where to go on: the last episode at its place; the one after it once it was watched; else the first not watched
  function resumeTarget() {
    const eps = dub.episodes;
    if (!eps.length) return null;
    const last = progress.last ? eps.findIndex((e) => e.n === String(progress.last)) : -1;
    if (last >= 0) {
      if (!watched(eps[last].n)) {
        const p = placeOf(eps[last].n);
        return { n: eps[last].n, at: p ? p.at : 0, going: true };
      }
      if (last + 1 < eps.length) return { n: eps[last + 1].n, at: 0, going: true };
    }
    const next = eps.find((e) => !watched(e.n)) || eps[0];
    const p = placeOf(next.n);
    return { n: next.n, at: p ? p.at : 0, going: !!p };
  }

  function actions() {
    const holder = $("[data-wt-actions]");
    holder.textContent = "";
    const target = resumeTarget();
    if (target) {
      const go = el("button", "wt-btn primary wt-resume");
      go.type = "button";
      go.append(icon("play", 16, true), el("span", "", (target.going ? "Продолжить: серия " : "Смотреть: серия ") +
        target.n + (target.at > 5 ? " с " + clock(target.at) : "")));
      go.addEventListener("click", () => {
        play(target.n, { at: target.at });
        $("[data-wt-player]").scrollIntoView({ behavior: "smooth", block: "center" });
      });
      holder.append(go);
    }
    const get = el("a", "wt-btn");
    get.href = getPage + "?url=" + encodeURIComponent(T.address) + "&go=1";
    get.append(icon("down", 16), el("span", "", "Скачать…"));
    holder.append(get);
    const links = el("div", "wt-side-links");
    const site = el("a", "wt-link", T.site + " ↗");
    site.href = T.address;
    site.target = "_blank";
    site.rel = "noopener noreferrer";
    links.append(site);
    holder.append(links);
  }

  // ── the voice-over, the player, the quality ────────────────────────────────────────────────────────────────────
  const KIND_WORDS = { dub: "Дубляж", voice: "Озвучка", subs: "Субтитры" };
  const QUALITY_WORDS = { "": "Лучшее", "1080": "1080p", "720": "720p", "480": "480p", "360": "360p" };

  function popover(name, value, fill) {
    const wrap = el("div", "wt-pick");
    const button = el("button", "wt-pick-btn");
    button.type = "button";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    const words = el("span", "wt-pick-words");
    const valueNode = el("b", "wt-pick-value");
    words.append(el("span", "wt-pick-name", name), valueNode);
    button.append(words, icon("chevron", 16));
    const panel = el("div", "wt-pop");
    panel.hidden = true;
    let open = false;
    function toggle(on) {
      open = !!on;
      panel.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
      wrap.classList.toggle("open", open);
      if (open) {
        panel.textContent = "";
        fill(panel, () => { toggle(false); button.focus(); });
        const first = panel.querySelector("input, [aria-selected='true'], [role='option']");
        if (first) first.focus();
      }
    }
    button.addEventListener("click", () => toggle(!open));
    document.addEventListener("click", (event) => { if (open && !wrap.contains(event.target)) toggle(false); });
    wrap.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && open) {
        event.preventDefault();
        toggle(false);
        button.focus();
      } else if (open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
        const options = [...panel.querySelectorAll("[role='option']")].filter((o) => !o.hidden && o.offsetParent);
        if (!options.length) return;
        event.preventDefault();
        const at = options.indexOf(document.activeElement);
        const next = event.key === "ArrowDown" ? Math.min(options.length - 1, at + 1) : Math.max(0, at - 1);
        options[at < 0 ? 0 : next].focus();
      }
    });
    wrap.append(button, panel);
    const set = (text) => { valueNode.textContent = text; };
    set(value);
    return { node: wrap, set, close: () => toggle(false) };
  }

  function option(text, chosen, extra) {
    const o = el("button", "wt-option");
    o.type = "button";
    o.setAttribute("role", "option");
    o.setAttribute("aria-selected", chosen ? "true" : "false");
    o.append(el("span", "wt-option-name", text));
    (extra || []).forEach((x) => { if (x) o.append(x); });
    return o;
  }

  let pickers = {};
  function playerWords() {
    const p = dub.players.find((x) => x.key === player);
    if (p) return p.name + " · " + plural(p.count, ["сер.", "сер.", "сер."]);
    return "Авто" + (stream && stream.dub === dub.name ? " · " + stream.player_name : "");
  }
  function qualityWords() {
    return QUALITY_WORDS[quality] + (stream && stream.quality && stream.dub === dub.name &&
      stream.quality !== QUALITY_WORDS[quality] ? " · " + stream.quality : "");
  }
  function dubWords(d) { return d.short + " · " + d.episodes.length + " сер."; }
  function dubName(d) { return d.plain ? siteName : d.short; }

  function picks() {
    const holder = $("[data-wt-picks]");
    holder.textContent = "";
    pickers = {};
    // (v5.44.109) only what the title has to choose: one voice-over the site names no one for, no players (a page's
    // own files), no qualities — no picker
    const choices = T.dubs.length > 1 || !T.dubs[0].plain;
    const players = T.dubs.some((d) => (d.players || []).length);
    const qualities = (T.qualities || []).length > 0;
    if (choices) pickers.dub = popover("Озвучка", dubWords(dub), (panel, close) => {
      const search = el("input", "wt-search");
      search.type = "search";
      search.placeholder = "Найти среди " + plural(T.dubs.length, ["озвучки", "озвучек", "озвучек"]);
      search.setAttribute("aria-label", "Найти озвучку");
      const kinds = el("div", "wt-kinds");
      kinds.setAttribute("role", "group");
      kinds.setAttribute("aria-label", "Вид");
      const list = el("div", "wt-options");
      list.setAttribute("role", "listbox");
      list.setAttribute("aria-label", "Озвучки");
      let kind = "";
      const counts = {};
      T.dubs.forEach((d) => { counts[d.kind] = (counts[d.kind] || 0) + 1; });
      [["", "Все"]].concat(Object.keys(KIND_WORDS).filter((k) => counts[k]).map((k) => [k, KIND_WORDS[k] + " " + counts[k]]))
        .forEach(([key, words]) => {
          const b = el("button", "wt-kind", words);
          b.type = "button";
          b.setAttribute("aria-pressed", key === kind ? "true" : "false");
          b.addEventListener("click", () => {
            kind = key;
            kinds.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
            draw();
          });
          kinds.append(b);
        });
      function draw() {
        const q = search.value.trim().toLowerCase();
        list.textContent = "";
        let shown = 0;
        Object.keys(KIND_WORDS).forEach((k) => {
          if (kind && kind !== k) return;
          const items = T.dubs.filter((d) => d.kind === k && (!q || d.name.toLowerCase().includes(q)));
          if (!items.length) return;
          if (!kind) list.append(el("div", "wt-options-head", KIND_WORDS[k]));
          items.forEach((d) => {
            const have = d.have ? el("span", "wt-badge", "в папке " + d.have) : null;
            const o = option(d.short, d === dub, [have, el("span", "wt-count", d.episodes.length + " сер.")]);
            o.title = d.name + " — " + d.players.map((p) => p.name).join(", ");
            o.addEventListener("click", () => { close(); chooseDub(d); });
            list.append(o);
            shown += 1;
          });
        });
        if (!shown) list.append(el("p", "wt-muted wt-none", "Ничего не нашлось"));
      }
      search.addEventListener("input", draw);
      panel.append(search, kinds, list);
      draw();
    });
    if (players) pickers.player = popover("Плеер", playerWords(), (panel, close) => {
      const list = el("div", "wt-options");
      list.setAttribute("role", "listbox");
      list.setAttribute("aria-label", "Плееры");
      const auto = option("Авто", player === "", [el("span", "wt-count", "лучшее в выбранном качестве")]);
      auto.addEventListener("click", () => { close(); choosePlayer(""); });
      list.append(auto);
      dub.players.forEach((p) => {
        const o = option(p.name, p.key === player, [el("span", "wt-count", "до " + p.best + "p · " + p.count + " сер.")]);
        o.addEventListener("click", () => { close(); choosePlayer(p.key); });
        list.append(o);
      });
      panel.append(list);
    });
    if (qualities) pickers.quality = popover("Качество", qualityWords(), (panel, close) => {
      const list = el("div", "wt-options");
      list.setAttribute("role", "listbox");
      list.setAttribute("aria-label", "Качество");
      Object.keys(QUALITY_WORDS).forEach((key) => {
        const o = option(key ? "не выше " + QUALITY_WORDS[key] : "Лучшее", key === quality);
        o.addEventListener("click", () => { close(); chooseQuality(key); });
        list.append(o);
      });
      panel.append(list);
    });
    const downloads = el("div", "wt-downloads");
    const one = el("button", "wt-btn");
    one.type = "button";
    one.dataset.wtGetOne = "";
    const rest = el("button", "wt-btn quiet");
    rest.type = "button";
    rest.dataset.wtGetRest = "";
    one.addEventListener("click", () => downloadEpisodes([one.dataset.n]));
    rest.addEventListener("click", () => downloadEpisodes(JSON.parse(rest.dataset.list || "[]")));
    downloads.append(one, rest);
    const note = el("p", "wt-get-note");
    note.dataset.wtGetNote = "";
    note.setAttribute("role", "status");
    note.hidden = true;
    [pickers.dub, pickers.player, pickers.quality].forEach((p) => { if (p) holder.append(p.node); });
    holder.append(downloads, note);
    downloadButtons();
  }

  function refreshPicks() {
    if (pickers.dub) pickers.dub.set(dubWords(dub));
    if (pickers.player) pickers.player.set(playerWords());
    if (pickers.quality) pickers.quality.set(qualityWords());
    downloadButtons();
  }

  function downloadButtons() {
    const one = $("[data-wt-get-one]"), rest = $("[data-wt-get-rest]");
    if (!one) return;
    const target = current && dub.episodes.some((e) => e.n === current) ? current : (resumeTarget() || {}).n;
    const ep = dub.episodes.find((e) => e.n === target);
    const missing = dub.episodes.filter((e) => !e.have || e.have === "part");
    one.dataset.n = target || "";
    one.textContent = "";
    one.append(icon("down", 15), el("span", "", ep && (ep.have === "whole" || ep.have === "other") ? "Серия " + target + " в папке"
      : "Скачать серию " + (target || "")));
    one.disabled = !ep || ep.have === "whole" || ep.have === "other";
    rest.dataset.list = JSON.stringify(missing.map((e) => e.n));
    rest.textContent = "";
    rest.append(icon("down", 15), el("span", "", missing.length ? "Скачать остальные " + missing.length : "Все серии в папке"));
    rest.disabled = !missing.length;
  }

  function choose(changes) {
    post("/api/watch/choose", Object.assign({ url }, changes)).catch(() => {});
  }
  function chooseDub(d) {
    if (d === dub) return;
    dub = d;
    if (!dub.players.some((p) => p.key === player)) player = "";
    choose({ dub: dub.name, player });
    refreshPicks();
    tiles();
    actions();
    if (current && dub.episodes.some((e) => e.n === current) && stream) {
      play(current, { at: position, keepPaused: !playing });          // the same episode in the other voice-over
    } else if (current) {
      stopPlayer();
      current = "";
      $("[data-wt-now]").textContent = "Выберите серию — в озвучке «" + dubName(dub) + "» её нет";
      paintTiles();
      nav();
    }
  }
  function choosePlayer(key) {
    if (key === player) return;
    player = key;
    choose({ player });
    refreshPicks();
    if (current && stream) play(current, { at: position, keepPaused: !playing });
  }
  function chooseQuality(key) {
    if (key === quality) return;
    quality = key;
    choose({ quality: key || "best" });
    refreshPicks();
    if (current && stream) play(current, { at: position, keepPaused: !playing });
  }

  // ── the episodes ───────────────────────────────────────────────────────────────────────────────────────────────
  function tiles() {
    const grid = $("[data-wt-eps]");
    grid.textContent = "";
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-label", "Серии");
    $("[data-wt-count]").textContent = String(dub.episodes.length);
    dub.episodes.forEach((e) => {
      const b = el("button", "wt-ep");
      b.type = "button";
      b.dataset.n = e.n;
      b.append(el("span", "wt-ep-check"), el("span", "wt-ep-n", e.n), el("span", "wt-ep-bar"));
      b.querySelector(".wt-ep-check").append(icon("check", 12));
      b.addEventListener("click", () => play(e.n, {}));
      grid.append(b);
    });
    paintTiles();
  }
  function paintTiles() {
    if (!dub) return;
    $("[data-wt-eps]").querySelectorAll(".wt-ep").forEach((b) => {
      const e = dub.episodes.find((x) => x.n === b.dataset.n);
      if (!e) return;
      const have = e.have === "whole" || e.have === "other";
      const seen = watched(e.n);
      const p = placeOf(e.n);
      const now = e.n === current;
      b.classList.toggle("have", have);
      b.classList.toggle("seen", seen);
      b.classList.toggle("now", now);
      const share = seen ? 100 : p && p.length > 0 ? Math.min(100, Math.round(100 * p.at / p.length)) : 0;
      b.querySelector(".wt-ep-bar").style.width = share + "%";
      b.setAttribute("aria-current", now ? "true" : "false");
      b.setAttribute("aria-label", "Серия " + e.n + (have ? ", в папке" : e.have === "part" ? ", начата в папке" : "")
        + (seen ? ", просмотрена" : p ? ", остановились на " + clock(p.at) : "") + (now ? ", сейчас" : ""));
      b.title = b.getAttribute("aria-label");
    });
  }
  function neighbour(step) {
    const eps = dub ? dub.episodes : [];
    const at = eps.findIndex((e) => e.n === current);
    if (at < 0) return null;
    return eps[at + step] || null;
  }
  function nav() {
    const prev = $("[data-wt-prev]"), next = $("[data-wt-next]");
    const before = neighbour(-1), after = neighbour(1);
    prev.disabled = !before;
    next.disabled = !after;
    prev.textContent = before ? "‹ Серия " + before.n : "‹ Предыдущая";
    next.textContent = after ? "Серия " + after.n + " ›" : "Следующая ›";
  }
  $("[data-wt-prev]").addEventListener("click", () => { const e = neighbour(-1); if (e) play(e.n, {}); });
  $("[data-wt-next]").addEventListener("click", () => { const e = neighbour(1); if (e) play(e.n, {}); });
  const autonext = $("[data-wt-autonext]");
  autonext.addEventListener("change", () => choose({ autonext: autonext.checked }));

  // ── the player ─────────────────────────────────────────────────────────────────────────────────────────────────
  const video = $("[data-wt-video]");
  const nbox = inApp ? $(".native-video[data-stream]") : null;
  const native = inApp ? LB.nativeVideo : null;
  const problemLine = $("[data-wt-problem]");
  let hls = null;

  function nowLine(text) { $("[data-wt-now]").textContent = text; }
  function problem(text) {
    if (problemLine) {
      problemLine.textContent = text || "";
      problemLine.hidden = !text;
    } else if (text) {
      nowLine(text);
    }
  }

  function stopPlayer() {
    if (hls) { hls.destroy(); hls = null; }
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    if (native && nbox && nbox.dataset.stream) native.stop();
    playing = false;
  }

  async function play(n, opts) {
    if (!dub) return;
    const ep = dub.episodes.find((e) => e.n === String(n));
    if (!ep) return;
    report(true);                                     // where the episode before stopped
    const asked = ++opening;
    current = ep.n;
    paintTiles();
    nav();
    downloadButtons();
    problem("");
    nowLine("Серия " + ep.n + " · " + dubName(dub) + " · открываю…");
    stopPlayer();
    let reply;
    try {
      reply = await post("/api/watch/open", { url, dub: dub.name, ep: ep.n, player, quality: quality || "" });
    } catch (err) {
      if (asked !== opening) return;
      if (err.data && err.data.hidden && hiddenByMode()) return;
      nowLine("Серия " + ep.n + " не открылась: " + err.message);
      return;
    }
    if (asked !== opening) return;
    stream = reply;
    endedFor = "";
    position = 0;
    duration = Number(reply.length) || 0;
    reported = Date.now();
    const at = opts.at !== undefined ? Number(opts.at) || 0 : Number(reply.from) || 0;
    const have = ep.have === "whole" || ep.have === "other";
    nowLine(["Серия " + ep.n, dubName(dub), reply.player_name !== dubName(dub) ? reply.player_name : "", reply.quality || "",
      have ? "есть и в папке" : "онлайн, не скачана"].filter(Boolean).join(" · "));
    refreshPicks();
    marks();
    if (inApp && native && nbox) {
      nbox.dataset.stream = reply.token;
      nbox.dataset.from = String(at);
      if (duration) nbox.dataset.length = String(duration);
      native.start();
    } else if (video) {
      playIn(reply, at, !opts.keepPaused);
    }
  }

  function playIn(info, at, go) {
    let retried = false;
    const begin = () => {
      if (at > 1) {
        try { video.currentTime = at; } catch (_) { /* from the start */ }
      }
    };
    video.preload = "auto";                    // (the page's <video> waits with «none» until an episode is chosen)
    if (info.kind === "hls" && window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ enableWorker: false, startPosition: at > 1 ? at : -1, maxBufferLength: 45,
                              backBufferLength: 60 });
      hls.on(window.Hls.Events.ERROR, (_event, data) => {
        if (!data || !data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR && !retried) {
          retried = true;
          hls.startLoad();
          return;
        }
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && !retried) {
          retried = true;
          hls.recoverMediaError();
          return;
        }
        problem("Видео не показывается: " + (data.details || "ошибка потока") +
          ". Попробуйте другой плеер или качество.");
      });
      hls.loadSource(info.src);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => { if (go) video.play().catch(() => {}); });
    } else if (info.kind === "mp4" || video.canPlayType("application/vnd.apple.mpegurl")) {
      video.addEventListener("loadedmetadata", begin, { once: true });
      video.src = info.src;
      if (go) video.play().catch(() => { /* the browser waits for a press: its own ▶ */ });
    } else {
      problem("Этот браузер не показывает потоки HLS — откройте страницу в окне программы или в другом браузере");
    }
  }

  if (video) {
    video.addEventListener("timeupdate", () => {
      position = video.currentTime || 0;
      duration = video.duration && isFinite(video.duration) ? video.duration : duration;
      skips();
      report(false);
    });
    video.addEventListener("play", () => { playing = true; problem(""); });
    video.addEventListener("pause", () => { playing = false; report(true); });
    video.addEventListener("ended", () => { playing = false; finished(); });
    video.addEventListener("error", () => {
      if (!video.getAttribute("src") || hls) return;
      problem("Видео не показывается этим браузером (" + ((video.error && video.error.message) || "формат") +
        "). Попробуйте другой плеер.");
    });
  }
  if (nbox) {
    nbox.addEventListener("lb-video-state", (event) => {
      const st = event.detail || {};
      position = Number(st.position) || 0;
      duration = Number(st.duration) || duration;
      playing = !!st.playing;
      skips();
      report(false);
      if (st.ended) finished();
    });
    // the big ▶ before an episode is chosen: the one to go on with (booru.js would say «выберите серию»)
    nbox.addEventListener("click", (event) => {
      if (nbox.dataset.stream || !event.target.closest("[data-action='nv-toggle']")) return;
      event.preventDefault();
      event.stopPropagation();
      const target = resumeTarget();
      if (target) play(target.n, { at: target.at });
    }, true);
  }

  function finished() {
    if (!stream || endedFor === stream.token) return;
    endedFor = stream.token;
    report(true, true);
    const after = neighbour(1);
    if (after && autonext.checked) {
      nowLine("Дальше — серия " + after.n);
      setTimeout(() => play(after.n, { at: 0 }), 1200);
    }
  }

  // where the episode stopped: every 15 s while it plays, on a pause, at its end, when the page goes
  function report(now, ended) {
    if (!stream || !current) return;
    if (!now && Date.now() - reported < 15000) return;
    if (!ended && position < 1) return;
    reported = Date.now();
    const ending = stream.skips && Array.isArray(stream.skips.ending) ? Number(stream.skips.ending[0]) || 0 : 0;
    // (the player and the quality chosen are kept by «choose»: «Авто» stays «Авто» whatever player gave the episode)
    const body = { url, ep: stream.ep, at: position, length: duration, ended: !!ended, dub: stream.dub, ending };
    post("/api/watch/progress", body).then((reply) => {
      if (reply && reply.progress) {
        progress = reply.progress;
        paintTiles();
        if (!playing) actions();
      }
    }).catch(() => { /* asked again with the next moment */ });
  }
  window.addEventListener("pagehide", () => {
    if (!stream || !current || position < 1) return;
    const body = JSON.stringify({ url, ep: stream.ep, at: position, length: duration, ended: false, dub: stream.dub });
    try {
      navigator.sendBeacon("/api/watch/progress", new Blob([body], { type: "application/json" }));
    } catch (_) { /* the last moment sent stays */ }
  });

  // «Пропустить опенинг / эндинг» while it goes (YummyAnime's times of the episode in its player)
  function skips() {
    const known = (stream && stream.skips) || {};
    box.querySelectorAll("[data-wt-skip]").forEach((button) => {
      const span = known[button.dataset.wtSkip];
      const on = Array.isArray(span) && position >= span[0] && position < span[1] - 1 && !!current;
      button.hidden = !on;
    });
  }
  box.querySelectorAll("[data-wt-skip]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const span = ((stream && stream.skips) || {})[button.dataset.wtSkip];
      if (!Array.isArray(span)) return;
      button.hidden = true;
      if (inApp && native) native.seekTo(span[1]);
      else if (video) video.currentTime = span[1];
    });
  });
  function marks() {
    const line = $("[data-wt-marks]");
    if (!line) return;
    line.textContent = "";
    const known = (stream && stream.skips) || {};
    const length = Number(stream && stream.length) || 0;
    let any = false;
    if (length > 0) {
      ["opening", "ending"].forEach((kind) => {
        const span = known[kind];
        if (!Array.isArray(span)) return;
        const mark = el("span", "wt-mark");
        mark.style.left = (100 * span[0] / length).toFixed(2) + "%";
        mark.style.width = (100 * Math.max(0, span[1] - span[0]) / length).toFixed(2) + "%";
        mark.title = (kind === "opening" ? "Опенинг " : "Эндинг ") + clock(span[0]) + "–" + clock(span[1]);
        line.append(mark);
        any = true;
      });
    }
    line.hidden = !any;
  }

  // ── «Скачать серию N», «Скачать остальные» — «Скачать» of the program (its find, then its download) ────────────
  let getting = false;
  async function downloadEpisodes(labels) {
    labels = (labels || []).filter(Boolean);
    if (!labels.length || getting) return;
    getting = true;
    const note = $("[data-wt-get-note]");
    const tell = (text, bad) => { note.hidden = !text; note.textContent = text || ""; note.classList.toggle("bad", !!bad); };
    box.querySelectorAll("[data-wt-get-one], [data-wt-get-rest]").forEach((b) => { b.disabled = true; });
    try {
      tell("Готовлю скачивание: программа ищет серии тайтла…");
      const started = await post("/api/videos/find", { url: T.address });
      let state = null;
      for (let after = 0; ;) {
        state = await getJson("/api/videos/find-state?after=" + after);
        after = Number(state.last_seq) || after;
        if (String(state.id) !== String(started.id)) throw new Error("Поиск сменился другим — нажмите ещё раз");
        if (state.status === "done" || state.status === "failed" || state.status === "stopped") break;
        await sleep(700);
      }
      if (!state.found) throw new Error(state.error || "Серии для скачивания не нашлись");
      const want = new Set();
      labels.forEach((n) => {
        const ep = dub.episodes.find((e) => e.n === String(n));
        if (ep) want.add(ep.group + "\u0000" + ep.title);
      });
      const files = state.found.files.filter((f) => want.has((f.group || "") + "\u0000" + (f.title || "")) &&
        f.have !== "whole" && f.have !== "other").map((f) => f.i);
      if (!files.length) throw new Error("Эти серии уже в папке");
      const reply = await post("/api/videos/download", { find: state.id, files, quality: quality || "best" });
      tell((reply.queued ? "В очереди скачивания" + (reply.queued > 1 ? " " + reply.queued + "-м" : "") + " — начнётся само. "
        : "Скачивание началось. ") + "Его видно в «Скачать» справа, в «Загрузках».");
    } catch (err) {
      tell(err.message, true);
    } finally {
      getting = false;
      downloadButtons();
    }
  }

  boot(false);
})();
