// Local Booru — (v5.44.121) one page of a series and one player of an episode (webui/pages/video_series.py; the owner's
// mockup D «Видео: одна страница сериала и один плеер»).  The page gets the series as a model (inline, then
// /api/series while its site is read): «Откуда серии» (the folder, «Смотреть онлайн», the voice-over of the series),
// the episodes — the folder's and the site's in one list, each with its variants (▾), «Смотреть» / «Продолжить» —, and
// the player under them.  An episode of the folder plays its file from the disk, an episode of the site — its stream
// through the program (/api/series/play); what was watched goes to the one memory (/api/series/progress).
// In the program's window an episode is a page of its own (?ep=…&play=1: the window's own player, booru.js
// nativeVideo); in a browser and on the phone the same <video> takes the next episode (the bar of the page: ⏮ ⏭, the
// number, the opening and the ending on it).
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
    const chips = $("[data-sv-chips]");
    if (chips && !chips.children.length && (M.genres || []).length) {
      M.genres.forEach((g) => {
        const li = el("li");
        const a = el("a", "", g);
        a.href = (phone ? "/m/videos?q=" : "/videos?tags=") + encodeURIComponent(g);
        li.append(a);
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
    if (M.series && !phone) {
      const reveal = el("button", "sv-btn quiet");
      reveal.type = "button";
      reveal.dataset.action = "vid-reveal";
      reveal.dataset.id = M.series;
      reveal.append(icon("folder", 16), el("span", "", "Показать в папке"));
      holder.append(reveal);
    }
  }

  // ── a title of a site without a folder here: «Скачать серию N», «Скачать остальные N» (or «Скачать все N») — the
  // machinery of «Скачать» (/api/videos/find, then /api/videos/download: the other chat's — only asked) ──
  let getting = false;
  function downloads() {
    const side = $(".sv-side");
    if (!side) return;
    let box = side.querySelector("[data-sv-gets]");
    const dub = (M.dubs || []).find((d) => d.name === M.dub);
    if (M.series || !dub || !(M.site && M.site.online)) { if (box) box.remove(); return; }
    if (!box) {
      box = el("span", "sv-gets");
      box.dataset.svGets = "";
      side.prepend(box);
    }
    box.textContent = "";
    const labels = Object.keys(dub.files || {});
    const missing = episodes().filter((e) => !e.disk.length && labels.includes(e.key)).map((e) => e.key);
    const target = current && labels.includes(current) ? current : (M.resume || {}).key;
    const one = el("button", "sv-link sv-get-one");
    one.type = "button";
    one.dataset.svGetOne = target || "";
    const inFolder = target && !missing.includes(target);
    one.textContent = inFolder ? "Серия " + target + " в папке" : "Скачать серию " + (target || "");
    one.disabled = !target || inFolder || getting;
    one.addEventListener("click", () => download([target], dub));
    const rest = el("button", "sv-link sv-get-rest");
    rest.type = "button";
    rest.dataset.svGetRest = "";
    // (v5.44.116) none in the folder yet — «Скачать все 12»; some — «Скачать остальные 9»
    rest.textContent = !missing.length ? "Все серии в папке"
      : (missing.length === labels.length ? "Скачать все " : "Скачать остальные ") + missing.length;
    rest.disabled = !missing.length || getting;
    rest.addEventListener("click", () => download(missing, dub));
    box.append(one, rest);
  }
  async function download(labels, dub) {
    labels = (labels || []).filter(Boolean);
    if (!labels.length || getting) return;
    getting = true;
    downloads();
    try {
      say("Готовлю скачивание: программа ищет серии тайтла…");
      const started = await post("/api/videos/find", { url: M.url });
      let state = null;
      for (let after = 0; ;) {
        state = await getJson("/api/videos/find-state?after=" + after);
        after = Number(state.last_seq) || after;
        if (String(state.id) !== String(started.id)) throw new Error("Поиск сменился другим — нажмите ещё раз");
        if (state.status === "done" || state.status === "failed" || state.status === "stopped") break;
        await sleep(700);
      }
      if (!state.found) throw new Error(state.error || "Серии для скачивания не нашлись");
      const want = new Set(labels.map((n) => ((dub.files || {})[n] || ["", ""]).join("\u0000")));
      const files = state.found.files.filter((f) => want.has((f.group || "") + "\u0000" + (f.title || "")) &&
        f.have !== "whole" && f.have !== "other").map((f) => f.i);
      if (!files.length) throw new Error("Эти серии уже в папке");
      const reply = await post("/api/videos/download", { find: state.id, files,
                                                         quality: (M.picks || {}).quality || "best" });
      say((reply.queued ? "В очереди скачивания" + (reply.queued > 1 ? " " + reply.queued + "-м" : "") + " — начнётся само. "
        : "Скачивание началось. ") + "Его видно в «Загрузках».");
    } catch (err) {
      say(err.message, true);
    } finally {
      getting = false;
      downloads();
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
    const c = M.counts || {};
    const head = el("div", "sv-src-head");
    const h = el(phone ? "h2" : "h2", "sv-src-h");
    h.append(icon("videos", 15), el("span", "", "Откуда серии"));
    head.append(h);
    if (!M.single) {
      const words = [plural(c.total || 0, EPISODES) + ":"];
      if (M.series) words.push((c.disk || 0) + " в папке");
      if (c.site) words.push((M.series ? "" : "") + c.site + " — только на сайте");
      head.append(el("span", "sv-src-note", words.join(" ").replace(": ", ": ").replace(/ в папке (\d)/, " в папке, $1")));
    }
    holder.append(head);
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
        const sites = document.documentElement.dataset.contentMode === "sfw" ? "YummyAnime" : "YummyAnime и Hentasis";
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
    if ((M.voices || []).length > 1 || (M.voices || []).some((v) => v.site.length)) {
      const dd = row("Озвучка", "voice");
      const now = currentVoice();
      const button = el("button", "sv-voice-now");
      button.type = "button";
      button.dataset.svVoice = "series";
      button.append(icon("voice", 14), el("b", "", now ? now.name : "как в файлах серий"));
      if (now) button.append(el("small", "", "· " + voiceLine(now)));
      button.append(icon("chev", 13));
      button.addEventListener("click", () => openVoices(null, button));
      dd.append(button);
      dd.append(el("p", "sv-src-line sv-muted", "выбрана у сериала — так и идёт дальше, со следующими сериями; у серии " +
        "можно выбрать другую" + (phone ? " — «Озвучка» в плеере серии" : " (▾ в строке серии)")));
    }
    holder.append(rows);
  }

  // ── the episodes ──────────────────────────────────────────────────────────────────────────────────────────────
  let newestFirst = false;
  try { newestFirst = localStorage.getItem("lb-sv-order") === "new"; } catch (_) { newestFirst = false; }
  let opened = "";                       // the episode whose variants are open (the computer: a line under its row)

  function marks(e) {
    const out = el("span", "sv-marks-row");
    if (e.done) {
      const m = el("span", "sv-mark read");
      m.append(icon("check", 12), "досмотрена");
      out.append(m);
    }
    if (e.new) out.append(el("span", "sv-mark new", "новая"));
    if (e.disk.length) {
      const m = el("span", "sv-mark have");
      m.append(icon("check", 12), "в папке");
      out.append(m);
    } else if (e.site.length) {
      const m = el("span", "sv-mark from");
      m.append(icon("globe", 12), "с сайта, онлайн");
      out.append(m);
    }
    return out;
  }
  function subLine(e) {
    if (e.here && e.place) return "Вы остановились здесь · " + clock(e.place[0]) + " из " + clock(e.place[1]);
    if (e.place) return "остановились на " + clock(e.place[0]) + (e.place[1] ? " из " + clock(e.place[1]) : "");
    if (e.done) return "досмотрена" + (e.seen ? " " + e.seen : "");
    if (!e.disk.length && e.site.length) {
      if (e.new) return "вышла на " + siteName();
      return "нет в папке — смотреть с сайта, без скачивания";
    }
    if (!e.disk.length && !e.site.length) return "нет в папке — включите «Смотреть онлайн»";
    return [e.note, e.length ? clock(e.length) : ""].filter(Boolean).join(" · ");
  }
  function voiceWords(e) {
    const v = e.chosen || {};
    if (!v.name) return { name: "—", where: "" };
    return { name: v.name, where: v.where === "site" ? "онлайн" : v.kind === "track" ? "в файле" : "в папке" };
  }

  function list() {
    const holder = $("[data-sv-list]");
    if (!holder || M.single) { if (holder) holder.hidden = true; return; }
    holder.textContent = "";
    const c = M.counts || {};
    const head = el("div", "sv-list-head");
    const h = el("h2", "", "Серии ");
    h.append(el("span", "sv-count", String(c.total || 0)));
    head.append(h);
    const order = el("button", "sv-link sv-order", newestFirst ? "↓ сначала новые" : "↑ по порядку");
    order.type = "button";
    order.addEventListener("click", () => {
      newestFirst = !newestFirst;
      try { localStorage.setItem("lb-sv-order", newestFirst ? "new" : "old"); } catch (_) { /* this page */ }
      list();
    });
    head.append(order);
    const legend = el(phone ? "p" : "ul", "sv-legend");
    const items = [];
    if (M.series) items.push("в папке — " + (c.disk || 0));
    if (c.site) items.push("только на сайте — " + c.site);
    items.push("досмотрено — " + (c.done || 0));
    if (c.new) items.push("новая — " + c.new);
    if (phone) legend.textContent = items.join(" · ");
    else items.forEach((t) => legend.append(el("li", "", t)));
    head.append(legend);
    holder.append(head);
    const rows = el("ol", "sv-rows");
    const all = newestFirst ? episodes().slice().reverse() : episodes();
    all.forEach((e) => {
      rows.append(row(e));
      if (!phone && opened === e.key) rows.append(variantsRow(e));
    });
    holder.append(rows);
  }

  function row(e) {
    const cls = ["sv-row"];
    if (e.done) cls.push("read");
    if (e.here) cls.push("here");
    if (!e.disk.length) cls.push("site");
    if (current && e.key === current) cls.push("now");
    const li = el("li", cls.join(" "));
    li.dataset.ep = e.key;
    const n = el("span", "sv-n", e.label);
    const name = el("div", "sv-row-name");
    name.append(el("span", "sv-row-title", phone ? (e.title || e.name) : nameOf(e)));
    const sub = subLine(e);
    if (sub) name.append(el("span", "sv-row-sub" + (e.here ? " here" : ""), sub));
    if (e.place && e.place[1]) {
      const bar = el("span", "sv-part");
      const fill = el("i");
      fill.style.width = Math.min(100, Math.round(100 * e.place[0] / e.place[1])) + "%";
      bar.append(fill);
      name.append(bar);
    }
    if (phone) name.append(marks(e));
    li.append(n, name);
    if (!phone) li.append(marks(e));
    const variants = e.disk.length + e.site.length;
    if (!phone) {
      const words = voiceWords(e);
      const dub = el("button", "sv-dub");
      dub.type = "button";
      dub.dataset.svDub = e.key;
      dub.setAttribute("aria-expanded", opened === e.key ? "true" : "false");
      const inside = el("span");
      inside.append(words.name + " ", el("small", "", words.where ? "· " + words.where : ""));
      dub.append(inside, icon("chev", 13));
      dub.disabled = variants < 2;
      dub.title = variants < 2 ? "У этой серии один вариант" : "В какой озвучке смотреть";
      dub.addEventListener("click", () => {
        opened = opened === e.key ? "" : e.key;
        list();
      });
      li.append(dub);
    }
    const go = el("a", phone ? "sv-read" : "sv-btn small " + (e.here ? "primary" : "quiet") + " sv-read");
    go.href = address(e.key, true);
    go.dataset.svWatch = e.key;                      // (not data-sv-play: that is the player's own box)
    go.setAttribute("aria-label", (e.here ? "Продолжить: " : "Смотреть: ") + e.name);
    go.append(icon("play", phone ? 20 : 14));
    if (!phone) go.append(el("span", "", e.here ? "Продолжить" : "Смотреть"));
    if (!playable(e)) {
      go.removeAttribute("href");
      go.setAttribute("aria-disabled", "true");
      go.classList.add("off");
    }
    go.addEventListener("click", (event) => {
      if (!playable(e)) { event.preventDefault(); return; }
      if (navigates()) return;
      event.preventDefault();
      choose(e.key, { autoplay: true });
    });
    li.append(go);
    return li;
  }

  // the variants of an episode: the computer — a line under its row; the phone — a sheet at the bottom of the screen
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
  function variantsRow(e) {
    const li = el("li", "sv-vars");
    const headText = el("p", "sv-vars-head");
    headText.append(el("b", "", e.name + " — в какой озвучке смотреть"), " · выбор помнится для сериала, следующие серии пойдут в нём же");
    li.append(headText);
    const { disk, site } = variantItems(e);
    if (disk.length) {
      const g = el("div", "sv-vars-group");
      const label = el("span", "sv-vars-label");
      label.append(icon("folder", 13), " В папке");
      g.append(label);
      disk.forEach((v) => g.append(variantButton(e, v)));
      li.append(g);
    }
    if (site.length) {
      const g = el("div", "sv-vars-group");
      const label = el("span", "sv-vars-label");
      label.append(icon("globe", 13), " С сайта, онлайн");
      g.append(label);
      const first = site.slice(0, 6);
      first.forEach((v) => g.append(variantButton(e, v)));
      if (site.length > first.length) {
        const more = el("button", "sv-link", "ещё " + (site.length - first.length));
        more.type = "button";
        more.addEventListener("click", () => {
          more.remove();
          site.slice(first.length).forEach((v) => g.append(variantButton(e, v)));
        });
        g.append(more);
      }
      li.append(g);
    } else if (M.site && M.site.can && !M.site.online) {
      li.append(el("p", "sv-vars-note", "Озвучки сайта — когда включено «Смотреть онлайн»"));
    }
    return li;
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
  function strip() {
    const holder = $("[data-sv-strip]");
    if (!holder || M.single) { if (holder) holder.hidden = true; return; }
    holder.textContent = "";
    if (phone) {
      const head = el("div", "sv-eps-head");
      head.append(el("h3", "", "Серии"));
      const legend = el("span", "", "✓ досмотрена");
      if ((M.counts || {}).site) legend.append(" · ", el("span", "sv-site-word", "• с сайта"));
      head.append(legend);
      holder.append(head);
    } else {
      holder.append(el("span", "sv-strip-label", "Серии"));
    }
    // the tabs of the episode the player shows (the row of numbers of the page of before: .vepisodes, .vep)
    const box = el("div", phone ? "sv-eps-grid" : "sv-eptabs vepisodes");
    box.setAttribute("role", "tablist");
    box.setAttribute("aria-label", "Серии");
    episodes().forEach((e) => {
      const b = el("a", "sv-ep vep" + (e.key === current ? " now" : e.done ? " done" : !e.disk.length ? " site" : ""), e.label);
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
    if (!phone) {
      const legend = el("span", "sv-strip-legend", "✓ досмотрена");
      if ((M.counts || {}).site) legend.append(el("span", "sv-site-word", " • с сайта, онлайн"));
      holder.append(legend);
    }
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
    list();
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
    list();
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
    downloads();
    sources();
    list();
    renderPlayer();
  }

  // ── the start ────────────────────────────────────────────────────────────────────────────────────────────────
  if (M.site && M.site.hidden && !M.series && hiddenByMode()) return;      // a title the mode hides (known already)
  render();
  if (M.site && M.site.pending) waitSite(false);
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
