// Local Booru — the page of a title of «Лента» (v5.44.102, webui/pages/title.py; the owner's approved mockup «Тайтл из
// «Онлайн»»).  The title is found by the machinery of «Скачать» (/api/manhwa/find → find-state; its cover —
// find-cover) and shown here: names, facts, the chapters' language or translators, «Продолжить» / «Читать с начала»
// (/online — without downloading), «Скачать всё», the chapters as rows, the newest first («▶ Читать», ticked —
// «Скачать отмеченные»), their state in the folder and while this title downloads (/api/manhwa/download-state).
// A gallery of nhentai — its facts, tags and pages (/api/manga/gallery), «Скачать» (/api/manga/download).
// «← Лента» goes back in the history when the feed was the page before (its place and its «Показать ещё» kept:
// catalog.js).  (v5.44.105) The phone's page (webui/pages/title.py, phone=True) — the same script: its reader and its
// card are its own (data-reader, data-get), its way back is the bar's (data-back of phone.js's frame); phone.js gives
// it LocalBooru (postJson; say — the phone's note at the bottom).
(() => {
  "use strict";
  const LB = window.LocalBooru;
  const box = document.getElementById("title-page");
  if (!LB || !box) return;
  const { postJson, say } = LB;
  const url = box.dataset.url;
  const rating = parseFloat(box.dataset.rating || "0") || 0;
  const $ = (selector) => box.querySelector(selector);
  const CHAPTERS = ["глава", "главы", "глав"];
  const PAGES = ["страница", "страницы", "страниц"];
  const RUNNING = ["starting", "running", "stopping"];
  const ICON = {
    play: "M8 5.5v13l10.5-6.5z",
    down: "M12 4v11m-5-5 5 5 5-5M5 20h14",
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svg(path, size) {
    const ns = "http://www.w3.org/2000/svg";
    const icon = document.createElementNS(ns, "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("width", String(size || 16));
    icon.setAttribute("height", String(size || 16));
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("class", "tt-icon");
    const line = document.createElementNS(ns, "path");
    line.setAttribute("d", path);
    icon.append(line);
    return icon;
  }

  function plural(n, forms) {
    const d = n % 10, h = n % 100;
    return n + " " + (d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2]);
  }

  function button(cls, text, icon) {
    const node = el("button", cls);
    node.type = "button";
    if (icon) node.append(svg(icon, 16));
    node.append(el("span", "", text));
    return node;
  }

  function link(cls, text, href, icon) {
    const node = el("a", cls);
    node.href = href;
    if (icon) node.append(svg(icon, 16));
    node.append(el("span", "", text));
    return node;
  }

  async function getJson(address) {
    const response = await fetch(address, { credentials: "same-origin", cache: "no-store" });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || ("Сервер ответил " + response.status));
      error.data = data;
      throw error;
    }
    return data;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function wait(text) {
    const w = $("[data-tt-wait]");
    w.hidden = !text;
    w.textContent = text || "";
    w.classList.remove("bad");
  }

  // the site did not give the title: its words, «Попробовать ещё раз»
  function fail(text, again) {
    const w = $("[data-tt-wait]");
    w.hidden = false;
    w.textContent = text + " ";
    w.classList.add("bad");
    if (again) {
      const retry = button("tt-link", "Попробовать ещё раз");
      retry.addEventListener("click", () => again());
      w.append(retry);
    }
    $(".tt-hero").removeAttribute("aria-busy");
  }

  // «← Лента»: the feed before — back in the history (its place, its «Показать ещё»), else the link.  The page
  // before is asked of the browser's history (the Navigation API): the program sends «Referrer-Policy: no-referrer»,
  // so document.referrer is empty (5.44.102 went by it — the link was always followed, the feed began anew) and the
  // history hides the addresses of its pages too — the feed marks its own entry (catalog.js: state «lbFeed»)
  // (v5.44.107) the feed anywhere before in the history, not only the page right before (the reader's «К тайтлу»,
  // another title opened from this one): back to it at once; none — its link, to the feed's last address, and the feed
  // takes its list back all the same (catalog.js: «lb-feed-return»)
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
        if (!path && entry.url) {                 // a browser that does show the address
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

  document.querySelectorAll("[data-tt-back], .m-top [data-back]").forEach((back) => {
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

  function chips(names, cls) {
    const list = el("ul", "tt-chips" + (cls ? " " + cls : ""));
    names.forEach((name) => list.append(el("li", "", name)));
    return list;
  }

  function about(text) {
    const node = el("div", "tt-desc");
    const words = el("p", "tt-desc-text", text);
    node.append(words);
    const more = button("tt-more", "Ещё");
    more.hidden = true;
    more.setAttribute("aria-expanded", "false");
    more.addEventListener("click", () => {
      const open = node.classList.toggle("open");
      more.querySelector("span").textContent = open ? "Свернуть" : "Ещё";
      more.setAttribute("aria-expanded", open ? "true" : "false");
    });
    node.append(more);
    requestAnimationFrame(() => { more.hidden = words.scrollHeight <= words.clientHeight + 2; });
    return node;
  }

  // the site's cover over the tile's: loaded aside, then put in its place — marked as come (motion.js hides a picture
  // of the page until its «load», and this one loads before it is on the page: «Плавные переходы» left it unseen)
  function cover(src) {
    if (!src) return;
    const holder = $("[data-tt-cover]");
    const img = new Image();
    img.alt = "";
    img.decoding = "async";
    img.onload = () => {
      img.classList.add("lb-in");
      if (holder.querySelector("img")) img.classList.add("lb-now");     // the same picture, larger: no fading
      holder.textContent = "";
      holder.append(img);
    };
    img.src = src;
  }

  // the mode: «SFW» — not a title of hentai, «NSFW» — hentai only (the server's words, without the title's name)
  function hiddenByMode(hentai) {
    const mode = box.dataset.mode;
    if ((mode === "sfw" && !hentai) || (mode === "nsfw" && hentai) || (mode !== "sfw" && mode !== "nsfw")) return false;
    const words = document.querySelector("template[data-tt-hidden]");
    if (!words) return false;
    document.title = "Скрыто — Local Booru";
    box.replaceWith(words.content.cloneNode(true));
    return true;
  }

  // ── a title of a site of manga ────────────────────────────────────────────────────────────────────────────────
  let findId = 0;
  let shown = null;          // the title shown: its find's answer
  let rows = new Map();      // a chapter's id → its row
  let order = "new";         // the newest first, as the mockup
  let watching = false;      // the downloads followed (one loop at a time)
  let running = null;        // a download going: {mine} — this title's or another's
  let waitFor = 0;           // a download asked here: until when it may still be about to show (ms)
  const missed = new Map();  // a chapter that did not come in a download of this page: its words

  async function find(branch, again) {
    wait("Читаю тайтл с сайта " + box.dataset.site + "…");
    const retry = () => find(branch, true).then((st) => { if (st) showTitle(st); });
    let id = 0;
    try {
      id = (await postJson("/api/manhwa/find", { url, branch: branch || "", again: !!again,
        name: box.dataset.name || "" })).id;                // (v5.44.112) the tile's name: the link's site 404 — by it
    } catch (err) {
      fail(err.message, retry);
      return null;
    }
    const until = Date.now() + 180000;
    let after = 0;
    while (Date.now() < until) {
      let st = null;
      try {
        st = await getJson("/api/manhwa/find-state?after=" + after);
      } catch (err) {
        await sleep(1000);
        continue;
      }
      if (st.id !== id) {                        // another title asked meanwhile (another page): this one is gone
        fail("Поиск этого тайтла сменился другим.", retry);
        return null;
      }
      after = st.last_seq || after;
      const said = (st.log || []).map((x) => x.text).filter(Boolean).pop();
      if (said && !st.found) wait(said);
      if (st.found) return st;
      if (st.failed) { fail(st.failed, retry); return null; }
      if (["done", "failed", "stopped"].includes(st.status)) {
        fail(st.error || "Тайтл не открылся.", retry);
        return null;
      }
      await sleep(500);
    }
    fail("Сайт отвечает слишком долго.", retry);
    return null;
  }

  function onlineHref(f, n, extra) {
    return (box.dataset.reader || "/online") + "?url=" + encodeURIComponent(f.url)
      + (f.branch ? "&branch=" + encodeURIComponent(f.branch) : "") + "&ch=" + n + (extra || "");
  }

  function showTitle(st) {
    const f = st.found;
    findId = st.id;
    shown = f;
    if (hiddenByMode(!!f.hentai)) return;
    $(".tt-hero").removeAttribute("aria-busy");
    wait("");
    if (f.cover) cover("/api/manhwa/find-cover?find=" + encodeURIComponent(findId));
    else if (f.cover_hidden) $("[data-tt-cover]").textContent = "";
    $("[data-tt-facts]").textContent = [f.type, f.year, f.status, f.age].filter(Boolean).join(" · ");
    $("[data-tt-name]").textContent = f.title;
    document.title = f.title + " — Local Booru";
    const alt = $("[data-tt-alt]");
    alt.textContent = (f.alt || []).join(" · ");
    alt.hidden = !alt.textContent;
    showNumbers(f);

    const holder = $("[data-tt-about]");
    holder.querySelectorAll(".tt-extra").forEach((node) => node.remove());
    const extra = el("div", "tt-extra");
    const branches = f.branches || [];
    if (branches.length > 1) extra.append(branchPicker(f, branches));
    (f.notes || []).forEach((note) => extra.append(el("p", "tt-note warn", note)));
    const actions = el("div", "tt-actions");
    extra.append(actions);
    const status = el("p", "tt-status action-status");
    status.setAttribute("role", "status");
    extra.append(status);
    if (f.about) extra.append(about(f.about));
    // (v5.44.112) the description and the facts — of the owner's first site that has them (Remanga first)
    if (f.about && f.about_site && f.about_site !== f.site_label) {
      extra.append(el("p", "tt-desc-from", `Описание — с ${f.about_site}, главы — с ${f.site_label}`));
    }
    if ((f.genres || []).length) extra.append(chips(f.genres));
    holder.append(extra);
    readButtons(f, actions);
    const all = button("tt-btn", "", ICON.down);
    all.dataset.ttAll = "1";
    all.addEventListener("click", () => download(null, all));
    actions.append(all);
    showList(f);
    paintCounts();
    follow();
  }

  function showNumbers(f) {
    const numbers = $("[data-tt-numbers]");
    numbers.textContent = "";
    const list = f.list || [];
    if (rating) numbers.append(el("span", "tt-rate", "★ " + rating.toFixed(1).replace(".", ",")));
    // the chapters of the list (with the other sites'), as «Главы N» below; the date — of the site's own newest one
    numbers.append(el("span", "", plural(list.length || f.chapters, CHAPTERS)));
    if (f.updated && !(list.length && list[list.length - 1].from)) numbers.append(el("span", "", "последняя — " + f.updated));
    numbers.append(el("span", "", "с " + f.site_label));
    if (f.have) numbers.append(el("span", "tt-have", "в папке: " + f.have));
  }

  function branchPicker(f, branches) {
    const wrap = el("div", "tt-branches");
    const label = f.languages ? "Язык глав" : "Переводчики";
    wrap.append(el("span", "tt-label", label));
    const group = el("div", "tt-pick");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", label);
    branches.forEach((b) => {
      const pick = button("tt-chip-btn", (b.name || b.id || "без названия") + " · " + b.count + " гл.");
      pick.setAttribute("aria-pressed", String(b.id) === String(f.branch) ? "true" : "false");
      pick.addEventListener("click", async () => {
        if (String(b.id) === String(f.branch)) return;
        group.querySelectorAll("button").forEach((x) => { x.disabled = true; });
        const st = await find(String(b.id));
        if (!st) {
          group.querySelectorAll("button").forEach((x) => { x.disabled = false; });
          return;
        }
        missed.clear();
        showTitle(st);
        try {                                      // F5 and «К тайтлу» of the reader — these translators again
          const here = new URL(location.href);
          here.searchParams.set("b", String(b.id));
          history.replaceState(history.state, "", here.pathname + here.search);
        } catch (_) { /* the address as it was */ }
      });
      group.append(pick);
    });
    wrap.append(group);
    return wrap;
  }

  function readButtons(f, actions) {
    const list = f.list || [];
    const first = list.findIndex((c) => !c.wait);
    if (first < 0) return;
    const start = link("tt-btn primary", "Читать с начала", onlineHref(f, first + 1, "&from=start"), ICON.play);
    actions.append(start);
    getJson("/api/online/last?url=" + encodeURIComponent(f.url)).then((reply) => {
      const last = reply && reply.last;
      if (!last || !last.ch || shown !== f) return;
      const n = last.done && last.ch < list.length ? last.ch + 1 : last.ch;
      const go = link("tt-btn primary", "Продолжить: " + ((list[n - 1] && list[n - 1].label) || last.label),
        onlineHref(f, n), ICON.play);
      start.className = "tt-btn";
      start.querySelector(".tt-icon").remove();
      actions.prepend(go);
    }).catch(() => { /* nothing read yet: «Читать с начала» only */ });
  }

  function chapterTitle(c) {
    return (c.tome ? "Том " + c.tome + " · " : "") + "Глава " + (c.n || "?") + (c.name ? " — " + c.name : "");
  }

  function stateOf(c) {
    if (c.have) return ["✓ в папке", "have"];
    if (missed.has(String(c.id))) return [missed.get(String(c.id)), "bad"];
    if (c.wait) return [c.free_at ? "платная до " + c.free_at : "бесплатно её нет нигде", "wait"];
    if (c.from) return ["с " + c.from, "from"];
    return ["", ""];
  }

  function showList(f) {
    const section = $("[data-tt-list]");
    section.textContent = "";
    rows = new Map();
    const list = f.list || [];
    const head = el("div", "tt-list-head");
    const h2 = el("h2", "", "Главы ");
    h2.id = "tt-list-head";
    h2.append(el("span", "tt-count", String(list.length)));
    const turn = button("tt-link", order === "new" ? "↓ сначала новые" : "↑ сначала первые");
    turn.addEventListener("click", () => {
      order = order === "new" ? "old" : "new";
      turn.querySelector("span").textContent = order === "new" ? "↓ сначала новые" : "↑ сначала первые";
      place();
    });
    const tools = el("div", "tt-list-tools");
    const tickAll = button("tt-btn small quiet", "Отметить все");
    tickAll.addEventListener("click", () => {
      const open = [...rows.values()].filter((r) => !r.tick.disabled);
      const on = open.some((r) => !r.tick.checked);
      open.forEach((r) => { r.tick.checked = on; });
      paintCounts();
    });
    const marked = button("tt-btn small", "Скачать отмеченные (0)", ICON.down);
    marked.dataset.ttMarked = "1";
    marked.addEventListener("click", () => download(ticked(), marked));
    tools.append(tickAll, marked);
    head.append(h2, turn, tools);
    const bar = el("div", "tt-running");
    bar.hidden = true;
    bar.setAttribute("role", "status");
    const queued = el("div", "tt-running tt-queued");     // (v5.44.107) this title in the queue: its place
    queued.hidden = true;
    queued.setAttribute("role", "status");
    const ol = el("ol", "tt-rows");
    section.append(head, bar, queued, ol);
    list.forEach((c, i) => {
      const row = el("li", "tt-row");
      row.dataset.id = c.id;
      const tick = el("input");
      tick.type = "checkbox";
      tick.disabled = !!(c.have || c.wait);
      tick.setAttribute("aria-label", "Отметить: " + chapterTitle(c));
      tick.addEventListener("change", paintCounts);
      const name = el("div", "tt-row-name");
      name.append(el("span", "tt-row-title", chapterTitle(c)));
      const [text, kind] = stateOf(c);
      const state = el("span", "tt-row-state" + (kind ? " " + kind : ""), text);
      row.append(tick, name, state);
      if (!c.wait) {
        const read = link("tt-btn small quiet tt-read", "Читать", onlineHref(f, i + 1), ICON.play);
        read.setAttribute("aria-label", "Читать: " + chapterTitle(c));
        row.append(read);
      } else {
        row.append(el("span", "tt-read-none"));
      }
      ol.append(row);
      rows.set(String(c.id), { row, tick, state, chapter: c });
    });
    place();
    section.hidden = false;
  }

  function place() {
    const ol = $(".tt-rows");
    if (!ol) return;
    const all = [...rows.values()].map((r) => r.row);
    (order === "new" ? all.reverse() : all).forEach((node) => ol.append(node));
  }

  function ticked() {
    return [...rows.values()].filter((r) => r.tick.checked && !r.tick.disabled).map((r) => r.chapter.id);
  }

  function paintCounts() {
    if (!shown) return;
    const list = [...rows.values()];
    const open = list.filter((r) => !r.tick.disabled).length;
    const some = list.some((r) => r.chapter.have);
    const paidLeft = list.some((r) => r.chapter.wait && !r.chapter.have);
    // nothing to take now, nothing in the folder yet: the title kept, its paid ones awaited by the nightly round
    const waits = !open && !some && paidLeft;
    const mine = !!(running && running.mine);
    const other = !!(running && !running.mine);     // another title downloads: this one goes into the queue
    const queuedRows = list.some((r) => r.queuedMark && !r.chapter.have);   // (v5.44.107) its chapters wait in the queue
    const all = $("[data-tt-all]");
    if (all) {
      all.querySelector("span").textContent = mine ? "Качается…"
        : open ? (other ? "В очередь — " : "Скачать всё — ") + plural(open, CHAPTERS)
          : queuedRows ? "В очереди" : waits ? "Ждать платные" : paidLeft ? "Бесплатные — в папке" : "Всё уже в папке";
      all.title = waits || (!open && paidLeft) ? "Платные программа скачает сама, когда они станут бесплатными" : "";
      all.disabled = mine || (!open && !waits);
    }
    const marked = $("[data-tt-marked]");
    if (marked) {
      // (v5.44.107) while this title downloads too: the chapters ticked go into the queue after it
      const n = ticked().length;
      marked.querySelector("span").textContent = (running ? "В очередь отмеченные" : "Скачать отмеченные") + " (" + n + ")";
      marked.disabled = !n;
    }
  }

  async function download(chapters, from) {
    const status = $(".tt-status");
    from.disabled = true;
    waitFor = Date.now() + 15000;                 // a loop following now goes on until the download shows
    const body = () => ({ find: findId, where: shown.kind, follow: true, helpers: shown.helpers_on !== false,
      chapters });
    try {
      let reply;
      try {
        reply = await postJson("/api/manhwa/download", body());
      } catch (err) {
        if (!/устарело/.test(err.message || "")) throw err;     // (the phone's postJson gives the words alone)
        const st = await find(String(shown.branch || ""));   // the search was another page's meanwhile: once more
        if (!st) return;
        showTitle(st);
        reply = await postJson("/api/manhwa/download", body());
      }
      (chapters || []).forEach((id) => missed.delete(String(id)));
      say(status, reply.queued ? "В очереди: начнётся, когда кончится текущее скачивание"
        : "Скачивание началось — его видно и в «Скачать → Загрузки»");
      rows.forEach((r) => { r.tick.checked = false; });
      follow(true);
    } catch (err) {
      waitFor = 0;
      say(status, err.message, true);
    } finally {
      from.disabled = false;
      paintCounts();
    }
  }

  // ── the downloads: this title's — its chapters' states on the rows; another's — «В очередь» here ──
  const WORDS = { wait: "в очереди", going: "качается", paused: "пауза", retry: "повтор", done: "✓ в папке",
    failed: "не пришла" };

  function paintItems(items) {
    items.forEach((x) => {
      const r = rows.get(String(x.id));
      if (!r) return;
      let text = WORDS[x.state] || "";
      if (x.state === "going" && x.pages) text = "качается " + x.got + "/" + x.pages;
      if (x.state === "retry" && x.again) text = "повтор через " + x.again + " с";
      if (x.state === "failed" && x.why) text = "не пришла: " + x.why;
      if (x.state === "failed") missed.set(String(x.id), text);
      else missed.delete(String(x.id));
      r.state.textContent = text;
      r.state.className = "tt-row-state " + (x.state === "done" ? "have" : x.state === "failed" ? "bad" : "going");
      r.queuedMark = false;
      if (x.state === "done") {
        r.tick.checked = false;
        r.tick.disabled = true;
        r.held = false;
        r.chapter.have = true;
      } else if (x.state === "failed") {
        if (r.held) { r.held = false; r.tick.disabled = false; }       // it can be ticked again
      } else if (!r.tick.disabled) {                // (v5.44.107) in the download: not ticked twice
        r.tick.checked = false;
        r.tick.disabled = true;
        r.held = true;
      }
    });
  }

  // (v5.44.107) this title waiting in the queue: its place, «Убрать», its chapters «в очереди» on their rows
  function paintQueued(queue) {
    const line = $(".tt-queued");
    const list = Array.isArray(queue) ? queue : [];
    const place = list.findIndex((q) => shown && q.url === shown.url && (!q.where || q.where === shown.kind));
    const entry = place >= 0 ? list[place] : null;
    const ids = new Set(entry ? (entry.chosen || []).map(String) : []);
    rows.forEach((r, id) => {
      if (ids.has(id) && !r.chapter.have) {
        r.state.textContent = "в очереди";
        r.state.className = "tt-row-state going";
        r.queuedMark = true;
        if (!r.tick.disabled) { r.tick.checked = false; r.tick.disabled = true; r.held = true; }
      } else if (r.queuedMark) {                    // it began, or it was taken out of the queue
        r.queuedMark = false;
        r.state.textContent = "";
        r.state.className = "tt-row-state";
        if (r.held) { r.held = false; r.tick.disabled = false; }
      }
    });
    if (!line) return;
    if (!entry) {
      line.hidden = true;
      return;
    }
    line.textContent = "";
    const ahead = place === 0 ? "следующим" : (place + 1) + "-м";
    line.append(el("span", "tt-running-words", "В очереди " + ahead + (entry.count ? ": " + plural(entry.count, CHAPTERS) : "")
      + " — начнётся, когда кончится текущее скачивание"));
    const drop = button("tt-link", "Убрать из очереди");
    drop.addEventListener("click", async () => {
      drop.disabled = true;
      try { await postJson("/api/manhwa/download/unqueue", { id: entry.id }); } catch (err) { say(line, err.message, true); }
      follow(true);
    });
    line.append(drop, link("tt-link", "Все загрузки", box.dataset.get || "/get"));
    line.hidden = false;
  }

  async function follow(now) {
    if (now) waitFor = Math.max(waitFor, Date.now() + 10000);   // it shows within seconds (or waits in the queue)
    if (watching) return;
    watching = true;
    let since = 0, run = 0, troubles = 0, sawOurs = false;
    try {
      for (;;) {
        let st;
        try {
          st = await getJson("/api/manhwa/download-state?after=999999999&since=" + since + "&run=" + run);
          troubles = 0;
        } catch (_) {
          troubles += 1;
          if (troubles > 20) break;
          await sleep(3000);
          continue;
        }
        const going = RUNNING.includes(st.status);
        const meter = st.meter || {};
        // this page's download — or this title's, begun before the page was opened again
        const same = !!shown && meter.site === shown.site && meter.title === shown.title;
        const ours = (!!findId && String(st.find) === String(findId)) || (going && same);
        const mine = going && ours;
        running = going ? { mine } : null;
        if (meter.run !== run) { since = 0; run = meter.run || 0; }
        since = Math.max(since, meter.rev || 0);
        if (ours) {
          sawOurs = true;
          paintItems(st.items || []);
          if (!going) waitFor = 0;                  // it has ended (perhaps before the first look): nothing to wait for
        }
        if (mine) showRunning(st, meter);
        else hideRunning();
        paintQueued(st.queue);
        paintCounts();
        const queued = Array.isArray(st.queue) ? st.queue.length : 0;
        if (!going && !queued && Date.now() >= waitFor) break;
        await sleep(mine ? 1200 : 2500);
      }
    } finally {
      watching = false;
      running = null;
      hideRunning();
      paintCounts();
    }
    if (sawOurs) refresh();
  }

  // this title's download has ended: the chapters as the folder has them now (the program reads the folder again)
  async function refresh() {
    let st;
    try {
      st = await getJson("/api/manhwa/find-state?after=999999999");
    } catch (_) {
      return;
    }
    if (!st.found || st.id !== findId) return;
    shown = st.found;
    showNumbers(shown);
    showList(shown);
    paintCounts();
  }

  function showRunning(st, meter) {
    const bar = $(".tt-running");
    if (!bar) return;
    bar.hidden = false;
    bar.textContent = "";
    const words = el("span", "tt-running-words", (st.paused ? "Пауза: " : "Качается: ") + (meter.done || 0) + " из "
      + (meter.total || 0) + (meter.failed ? ", не пришло " + meter.failed : ""));
    const pause = button("tt-link", st.paused ? "Продолжить" : "Пауза");
    pause.addEventListener("click", async () => {
      try { await postJson("/api/manhwa/download/pause", { pause: !st.paused }); } catch (err) { say(bar, err.message, true); }
    });
    const stop = button("tt-link", "Остановить");
    stop.addEventListener("click", async () => {
      try { await postJson("/api/manhwa/download/stop", {}); } catch (err) { say(bar, err.message, true); }
    });
    const all = link("tt-link", "Все загрузки", box.dataset.get || "/get");
    bar.append(words, pause, stop, all);
  }

  function hideRunning() {
    const bar = $(".tt-running");
    if (bar) bar.hidden = true;
  }

  // ── a gallery of nhentai ─────────────────────────────────────────────────────────────────────────────────────
  // (v5.44.107) where this gallery is in the download: its place in the queue, its pages coming, done
  let galleryWatch = 0;
  async function followGallery(id, status) {
    const mine = ++galleryWatch;
    let troubles = 0;
    for (;;) {
      await sleep(1500);
      if (mine !== galleryWatch) return;
      let st;
      try {
        st = await getJson("/api/manga/download-state");
        troubles = 0;
      } catch (_) {
        if (++troubles > 20) return;
        continue;
      }
      const going = RUNNING.includes(st.status);
      const item = (st.items || []).find((x) => String(x.number) === String(id));
      if (!item) {
        if (!going) return;
        continue;
      }
      const place = (st.queue || []).findIndex((q) => String(q.id) === String(id));
      let text = "";
      if (going && place >= 0) text = place === 0 ? "В очереди следующей" : "В очереди " + (place + 1) + "-й";
      else if (item.state === "reading") text = "Качается: читаю описание…";
      else if (item.state === "pages") text = "Качается: страница " + (item.page || 1) + " из " + (item.pages || "?");
      else if (item.state === "done" || item.state === "had") text = "✓ Скачана — в папке манги";
      else if (item.state === "failed") text = "Не скачалась" + (item.why ? ": " + item.why : "");
      else if (item.state === "stopped") text = "Остановлена — докачается в следующий раз";
      if (text) say(status, text, item.state === "failed");
      if (!going || ["done", "had", "failed", "stopped"].includes(item.state)) return;
    }
  }

  async function showGallery(id) {
    wait("Читаю галерею с nhentai…");
    let g;
    try {
      g = await getJson("/api/manga/gallery?id=" + encodeURIComponent(id));
    } catch (err) {
      fail(err.message, err.data && err.data.forbidden ? null : () => showGallery(id));
      return;
    }
    $(".tt-hero").removeAttribute("aria-busy");
    wait("");
    if (g.cover) cover("/api/catalog/cover?c=" + encodeURIComponent(g.cover));
    $("[data-tt-facts]").textContent = ["Хентай", "nhentai", g.date].filter(Boolean).join(" · ");
    $("[data-tt-name]").textContent = g.title;
    document.title = g.title + " — Local Booru";
    const alt = $("[data-tt-alt]");
    alt.textContent = [g.full !== g.title ? g.full : "", g.japanese !== g.title ? g.japanese : ""].filter(Boolean).join(" · ");
    alt.hidden = !alt.textContent;
    const numbers = $("[data-tt-numbers]");
    numbers.textContent = "";
    if (rating) numbers.append(el("span", "tt-rate", "★ " + rating.toFixed(1).replace(".", ",")));
    numbers.append(el("span", "", plural(g.pages, PAGES)), el("span", "", "№ " + g.id));
    if (g.favorites) numbers.append(el("span", "", "в избранном у " + g.favorites));
    $("[data-tt-about]").querySelectorAll(".tt-extra").forEach((node) => node.remove());
    const extra = el("div", "tt-extra");
    const actions = el("div", "tt-actions");
    const get = button("tt-btn primary", g.have && g.have.pages >= g.pages ? "Скачать заново" : "Скачать", ICON.down);
    const status = el("p", "tt-status action-status");
    status.setAttribute("role", "status");
    get.addEventListener("click", async () => {
      get.disabled = true;
      try {
        const reply = await postJson("/api/manga/download", { text: String(g.id) });
        // (v5.44.107) another gallery downloads: this one waits in the queue after it
        say(status, reply.already ? "Уже в «Загрузках»" : reply.queued ? "В очереди: начнётся, когда скачаются галереи "
          + "перед ней — её видно в «Скачать → Загрузки»" : "Скачивание началось — его видно в «Скачать → Загрузки»");
        followGallery(g.id, status);
      } catch (err) {
        say(status, err.message, true);
      }
      get.disabled = false;
    });
    actions.append(get);
    if (g.have && g.have.open) actions.append(link("tt-btn", "Открыть в программе", g.have.open));
    extra.append(actions, status);
    if (g.have) {
      extra.append(el("p", "tt-note", "Уже в папке манги: «" + g.have.folder + "»" + (g.have.pages >= g.pages
        ? " — все страницы на месте." : " — страниц там " + g.have.pages + " из " + g.pages + ".")));
    }
    (g.tags || []).forEach((group) => {
      const line = el("div", "tt-tags");
      line.append(el("span", "tt-label", group.kind));
      line.append(chips(group.names.slice(0, 40)));
      extra.append(line);
    });
    $("[data-tt-about]").append(extra);
    const thumbs = g.thumbs || [];
    if (!thumbs.length) return;
    const section = $("[data-tt-list]");
    section.textContent = "";
    const head = el("div", "tt-list-head");
    const h2 = el("h2", "", "Страницы ");
    h2.id = "tt-list-head";
    h2.append(el("span", "tt-count", String(thumbs.length)));
    head.append(h2);
    const grid = el("ol", "tt-pages");
    thumbs.forEach((c, k) => {
      const cell = el("li", "tt-page");
      const img = el("img");
      img.alt = "Страница " + (k + 1);
      img.loading = "lazy";
      img.decoding = "async";
      img.src = "/api/catalog/cover?c=" + encodeURIComponent(c);
      cell.append(img, el("span", "", String(k + 1)));
      grid.append(cell);
    });
    section.append(head, grid);
    section.hidden = false;
  }

  if (box.dataset.gallery) {
    showGallery(box.dataset.gallery);
  } else {
    find(box.dataset.branch || "").then((st) => { if (st) showTitle(st); });
  }
})();
