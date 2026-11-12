// Local Booru «Скачать → По ссылке» (v5.44.87, webui/pages/get_link.py): one field for every download.  A link or a
// name → what it is (/api/get/what: the mark in the field) → the card of what was found, from the program's own
// machinery by its addresses: the videos (video_get — the other chat's: its answers are only read, its download is
// its own) and nhentai (manga_get); (v5.44.119, the approved mockup A) a title of a site of manga — a link, or a title
// picked among the ones found by its name — opens the title's one page (webui/pages/title.py: /title?url=), no card
// of its own here any more.  On the right — (v5.44.120) the way to «Скачать → Загрузки» (downloads.js: every kind,
// one queue, «Готово»).  The phone's page of it — phone-get.js.
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const box = document.getElementById("get-link");
  if (!box || !LB.postJson) return;
  const postJson = LB.postJson;
  const form = box.querySelector('form[data-form="get-link"]');
  const field = form.querySelector('input[name="url"]');
  const findButton = form.querySelector(".gl-go");
  const mark = form.querySelector(".gl-kind");
  const step = box.querySelector(".gl-step");
  const card = box.querySelector(".gl-card");
  const hitsBox = box.querySelector(".gl-hits");
  const hitsTitle = hitsBox.querySelector("#gl-hits-title");
  const hitsSites = hitsBox.querySelector(".gl-hits-sites");
  const hitsList = hitsBox.querySelector(".gl-hits-list");
  const hitsFold = hitsBox.querySelector(".gl-hits-fold");
  const RUNNING = ["starting", "running", "stopping"];
  // (v5.44.120) what the cards ask of each kind; «Пауза», «Остановить», «Не продолжать» — «Скачать → Загрузки»
  // (downloads.js)
  const KINDS = {
    video: { label: "Видео", find: "/api/videos/find", findFile: "/api/videos/find-file",
             findState: "/api/videos/find-state", get: "/api/videos/download",
             state: "/api/videos/download-state", unqueue: "/api/videos/download/unqueue" },
    manhwa: { label: "Манга и манхва", find: "/api/manhwa/find", findState: "/api/manhwa/find-state",
              get: "/api/manhwa/download", state: "/api/manhwa/download-state", unqueue: "/api/manhwa/download/unqueue" },
    manga: { label: "Хентай", get: "/api/manga/download", state: "/api/manga/download-state",
             unqueue: "/api/manga/download/unqueue" },      // (v5.44.107) a queue
  };
  const ORDER = ["manhwa", "video", "manga"];
  const CHAPTERS = ["глава", "главы", "глав"];
  const TITLES = ["тайтл", "тайтла", "тайтлов"];

  // ── little things ─────────────────────────────────────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function button(cls, text, onClick) {
    const b = el("button", cls, text);
    b.type = "button";
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }

  // a button with an icon before its words: {node, words} — the words change, the icon stays
  function iconButton(cls, icon, text) {
    const b = button(cls);
    const words = el("span", "", text || "");
    b.append(svg(icon, 16), words);
    return { node: b, words };
  }

  function word(n, forms) {
    const d = n % 10, h = n % 100;
    return d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2];
  }
  const plural = (n, forms) => n + " " + word(n, forms);

  function megabytes(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1).replace(".", ",") + " ГБ";
    return Math.max(1, Math.round(n / 1024 / 1024)) + " МБ";
  }

  const natural = (a, b) => (parseFloat(a) - parseFloat(b)) || String(a).localeCompare(String(b), "ru");
  const folderName = (path) => String(path || "").split(/[\\/]/).filter(Boolean).pop() || String(path || "");

  function svg(paths, size, cls) {
    const ns = "http://www.w3.org/2000/svg";
    const icon = document.createElementNS(ns, "svg");
    const attrs = { class: "gl-svg" + (cls ? " " + cls : ""), viewBox: "0 0 24 24", width: String(size || 16),
      height: String(size || 16), fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round",
      "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" };
    Object.entries(attrs).forEach(([k, v]) => icon.setAttribute(k, v));
    paths.forEach((d) => {
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      icon.append(path);
    });
    return icon;
  }
  const ICON = {
    play: ["M7 4.5v15l12-7.5z"], check: ["M5 12.5l4.5 4.5L19 7.5"], lock: ["M7 11V8a5 5 0 0 1 10 0v3",
      "M5.5 11h13v9.5h-13z"], down: ["M12 4v11", "M7 10.5l5 5 5-5", "M5 19.5h14"], pause: ["M8 5v14", "M16 5v14"],
    stop: ["M6 6h12v12H6z"], close: ["M6 6l12 12", "M18 6L6 18"], open: ["M14 4h6v6", "M20 4l-9 9", "M18 14v6H4V6h6"],
  };

  function store(key, value) { try { sessionStorage.setItem(key, value); } catch (_) { /* a private window */ } }
  function stored(key) { try { return sessionStorage.getItem(key); } catch (_) { return null; } }
  function kept(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } }
  function keep(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* only this time */ } }

  async function getJson(address) {
    let response;
    try {
      response = await fetch(address, { credentials: "same-origin", cache: "no-store" });
    } catch (_) {
      throw new Error("Программа не отвечает");
    }
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) throw new Error(data.error || "Программа ответила ошибкой " + response.status);
    return data;
  }

  // (v5.44.117) the state of this page's own search: the titles of the sites — by its number (each page has its own
  // search; another page's «Найти» does not take this one's place); the videos — as their page asks (the other chat's)
  function findAddress(kind, id) {
    return KINDS[kind].findState + (kind === "manhwa" && id ? "?find=" + encodeURIComponent(id) : "");
  }
  const withAfter = (address, after) => address + (address.includes("?") ? "&" : "?") + "after=" + after;

  // a task's state with only the lines of its journal not seen yet (a long download has hundreds): a task not seen
  // before — asked without lines, then its newest line alone
  const marks = {};
  async function stateOf(key, address) {
    const m = marks[key] || (marks[key] = { id: null, seq: 999999999, line: "" });
    const ask = () => getJson(address + (address.includes("?") ? "&" : "?") + "after=" + m.seq);
    let st = await ask();
    if (st.id && st.id !== m.id) {               // another task: its newest line asked for alone
      m.id = st.id;
      m.line = "";
      m.seq = Math.max(0, (Number(st.last_seq) || 1) - 1);
      st = await ask();
    }
    const lines = st.log || [];
    if (lines.length) m.line = lines[lines.length - 1].text;
    if (st.last_seq) m.seq = st.last_seq;
    st.line = m.line;
    return st;
  }

  // ── the field: what it is, said in it as it is typed ───────────────────────────────────────────────────────────
  let markTimer = 0;
  let nhGen = 0;                 // (v5.44.97) a gallery's description asked: one that comes after another card — dropped
  let markGen = 0;               // «Найти» says what it found: a question asked before it does not answer over it

  function showMark(label, bad) {
    mark.textContent = label || "";
    mark.hidden = !label;
    mark.classList.toggle("bad", !!bad);
  }

  async function markKind() {
    const text = field.value.trim();
    const gen = ++markGen;
    if (!text) { showMark(""); return; }
    try {
      const w = await postJson("/api/get/what", { text });
      if (gen === markGen) showMark(w.label);
    } catch (err) {
      if (gen === markGen) showMark((err.data || {}).sfw ? (err.data || {}).label : "", true);
    }
  }

  field.addEventListener("input", () => {
    clearTimeout(markTimer);
    markTimer = setTimeout(markKind, 250);
  });

  // the line under the field: what goes on now, or why not
  function tell(text, bad, extra) {
    step.textContent = "";
    step.hidden = !text;
    if (!text) return;
    step.classList.toggle("bad", !!bad);
    if (!bad) step.append(el("span", "gl-spin"));
    step.append(el("span", "gl-step-text", text));
    if (extra) step.append(extra);
  }

  function tellError(err, kind) {
    const data = err.data || {};
    let extra = null;
    if (data.sfw || /«SFW»/.test(err.message)) {
      extra = button("gl-btn quiet small", "Показать всё");
      extra.dataset.action = "content-mode";
      extra.dataset.mode = "all";
      keepField(field.value.trim(), true);
    } else if (kind === "video") {
      // (v5.44.116) the sites — the server's lists (manhwa_get.SITES; the ones of hentai only outside «SFW»)
      const sites = box.dataset.sites || "", adultSites = box.dataset.adultSites || "";
      extra = el("span", "gl-step-more", (sites ? "Мангу и манхву программа берёт с " + sites
        + (adultSites ? ", хентай — с " + adultSites : "") + "; с других сайтов — только видео. " : "")
        + "Сайт не пускает программу — сохраните его страницу в браузере (Ctrl+S) и дайте её программе: ");
      extra.append(savedPicker());
    }
    tell(err.message, true, extra);
  }

  // (v5.44.89) a saved page of a site that does not let the program in (.html, .mhtml, .zip) — read by the machinery of
  // «Скачать с сайта» (``/api/videos/find-file``), its card as of a link: what the page of before alone had
  function savedPicker() {
    const label = el("label", "gl-file");
    const input = el("input", "gl-vh");
    input.type = "file";
    input.accept = ".html,.htm,.mhtml,.mht,.zip";
    input.dataset.savedPage = "1";
    label.append(input, el("span", "gl-btn quiet small", "Выбрать сохранённую страницу"));
    return label;
  }

  async function savedPage(file) {
    hideCard();
    busy(true);
    tell("Читаю сохранённую страницу…");
    try {
      const response = await fetch(KINDS.video.findFile + "?name=" + encodeURIComponent(file.name), {
        method: "POST", credentials: "same-origin", body: file,
        headers: { "Content-Type": "application/octet-stream" },
      });
      const reply = await response.json().catch(() => ({ ok: false, error: "Ответ " + response.status }));
      if (!response.ok || !reply.ok) throw new Error(reply.error || "Ответ " + response.status);
      finding = { kind: "video", id: reply.id, text: field.value.trim(), url: "" };
      waitFound();
    } catch (err) {
      busy(false);
      tell(err.message, true);
    }
  }

  box.addEventListener("change", (event) => {
    const input = event.target.closest && event.target.closest("input[data-saved-page]");
    if (!input || !input.files || !input.files[0]) return;
    const file = input.files[0];
    input.value = "";
    savedPage(file);
  });

  function busy(on) {
    findButton.disabled = on;
    findButton.textContent = on ? "Ищу…" : "Найти";
  }

  // the field kept in the page's address: a reload and «назад» find the card again (searched anew only with go=1)
  function keepField(text, again) {
    try {
      history.replaceState(history.state, "", "/get" + (text ? "?url=" + encodeURIComponent(text) + (again ? "&go=1" : "") : ""));
    } catch (_) { /* the address stays */ }
  }

  // ── «Найти» ───────────────────────────────────────────────────────────────────────────────────────────────────
  let finding = null;            // {kind, id, text, url} — a search going
  let shown = null;              // {kind, id, count?} — what the card shows

  // (v5.44.119) a title of a site — its one page (webui/pages/title.py, /title?url=: in the tab its title belongs to);
  // the field stays in this page's address and in «gl-last» — back here, the field (a link: «Открыть снова»; a name:
  // the titles found by it, the one picked marked), not that page again by itself
  function openTitle(url, text, hit) {
    store("gl-last", JSON.stringify({ kind: "title", text, url }));
    busy(true);
    tell("Открываю страницу тайтла…");
    const kind = hit ? (hit.kind === "manga" ? "manga" : "manhwa") : "";
    location.assign("/title?url=" + encodeURIComponent(url) + (kind ? "&k=" + kind : "")
      + (hit && hit.title ? "&t=" + encodeURIComponent(hit.title) : "") + (hit && hit.rating ? "&r=" + hit.rating : ""));
  }

  // back from a title's page: its link in the field, a line to open it again
  function titleLine(url) {
    step.textContent = "";
    step.classList.remove("bad");
    step.append(el("span", "gl-step-text", "Этот тайтл — на своей странице: "));
    const again = el("a", "gl-link", "открыть снова");
    again.href = "/title?url=" + encodeURIComponent(url);
    step.append(again);
    step.hidden = false;
  }

  // back from the title's page by the history, the page kept as it was left («Открываю…», «Найти» held): not busy
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted || finding) return;
    let last = null;
    try { last = JSON.parse(stored("gl-last") || "null"); } catch (_) { last = null; }
    if (!last || last.kind !== "title") return;
    busy(false);
    if (/^https?:\/\//i.test(field.value.trim())) titleLine(last.url || field.value.trim());
    else tell("");
  });

  // (v5.44.93) ``fresh`` — «Найти» pressed again for the card shown: asked anew (the program shows a title found a
  // while ago at once — manhwa_get._FOUND)
  async function ask(text, fresh) {
    text = String(text || "").trim();
    if (!text) { tell("Вставьте ссылку или напишите название", true); field.focus(); return; }
    keepField(text);
    finding = null;
    clearTimeout(markTimer);
    markGen += 1;
    busy(true);
    tell("Смотрю, что это…");
    let w;
    try {
      w = await postJson("/api/get/what", { text });
    } catch (err) {
      busy(false);
      tellError(err);
      return;
    }
    showMark(w.label);
    if (w.kind === "booru") {
      tell("Это пост booru-сайта — открываю «Галерея (booru)»…");
      location.assign(w.go);
      return;
    }
    if (w.kind === "name") {
      busy(false);
      tell("");
      searchName(w.q);
      return;
    }
    dropHits();
    hideCard();
    if (w.kind === "nhentai") {
      busy(false);
      tell("");
      showNhentai(w.ids);
      return;
    }
    if (w.kind === "site") {                     // (v5.44.119) its one page
      openTitle(w.url, text);
      return;
    }
    findLink("video", w.url, "", w.site, fresh);
  }

  async function findLink(kind, url, branch, siteLabel, fresh) {
    busy(true);
    tell(kind === "manhwa" ? (siteLabel ? siteLabel + ": ищу тайтл…" : "Ищу тайтл…") : "Читаю страницу…");
    try {
      const reply = await postJson(KINDS[kind].find, kind === "manhwa"
        ? { url, branch: branch || "", ...(fresh ? { again: true } : {}) } : { url });
      finding = { kind, id: reply.id, text: field.value.trim(), url };
      store("gl-last", JSON.stringify(finding));
      waitFound();
    } catch (err) {
      busy(false);
      tellError(err, kind);
    }
  }

  async function waitFound() {
    const mine = finding;
    if (!mine) return;
    let st;
    try {
      st = await stateOf("find-" + mine.kind, findAddress(mine.kind, mine.id));
    } catch (_) {
      if (finding === mine) setTimeout(waitFound, 1500);
      return;
    }
    if (finding !== mine) return;
    const going = RUNNING.includes(st.status);
    // (v5.44.117) the title so far: the link's site's chapters at once, the other sites' as they answer (the card is
    // drawn again each time something came — the chapters ticked stay ticked)
    if (st.id === mine.id && going && st.found) {
      if (!shown || shown.id !== st.id || shown.mark !== st.mark) {
        busy(false);
        tell("");
        showFound(mine.kind, st);
      }
      setTimeout(waitFound, 700);
      return;
    }
    if (st.id !== mine.id || going) {
      if (st.id === mine.id && st.line && !(shown && shown.id === st.id)) tell(st.line);
      setTimeout(waitFound, 600);
      return;
    }
    finding = null;
    busy(false);
    if (st.found) {
      tell("");
      if (!shown || shown.id !== st.id || shown.mark !== st.mark) showFound(mine.kind, st);
      return;
    }
    const why = st.failed || st.error || (st.status === "stopped" ? "Поиск остановлен" : "Не нашлось — проверьте ссылку");
    tellError(new Error(why), mine.kind);
  }

  function hideCard() {
    nhGen += 1;
    card.classList.remove("gl-busy");
    card.hidden = true;
    card.textContent = "";
    shown = null;
  }

  function showFound(kind, st) {
    if (kind !== "video") return;                // (v5.44.119) a title of a site has its own page (openTitle)
    nhGen += 1;
    card.classList.remove("gl-busy");
    card.textContent = "";
    card.dataset.kind = kind;
    shown = { kind, id: st.id, mark: st.mark || "", text: (finding && finding.text) || field.value.trim() };
    showVideo(st);
    card.hidden = false;
  }

  // ── the card: its cover, its names, its facts ────────────────────────────────────────────────────────────────
  function hero(cover, title, meta, alt) {
    const top = el("div", "gl-top");
    const pic = el("div", "gl-cover");
    const initial = el("span", "gl-initial", String(title || "?").trim().charAt(0).toUpperCase());
    initial.setAttribute("aria-hidden", "true");
    pic.append(initial);
    if (cover) {
      const img = el("img");
      img.alt = "";
      img.decoding = "async";
      img.onload = () => pic.classList.add("has-picture");
      img.onerror = () => img.remove();
      img.src = cover;
      pic.append(img);
    }
    const about = el("div", "gl-about");
    const facts = (meta || []).filter(Boolean);
    if (facts.length) {
      const line = el("p", "gl-meta");
      facts.forEach((x) => line.append(el("span", "", String(x))));
      about.append(line);
    }
    about.append(el("h2", "gl-title", title || "Без названия"));
    if (alt) about.append(el("p", "gl-alt", alt));
    top.append(pic, about);
    return { top, about, pic };
  }

  function chips(list, cls) {
    const ul = el("ul", "gl-chips" + (cls ? " " + cls : ""));
    (list || []).filter(Boolean).forEach((x) => ul.append(el("li", "", String(x))));
    return ul;
  }

  // (v5.44.130; the owner: «все плитки связанные с каким либо поиском» — clickable) chips that are links:
  // [{name, href, title}] (no href — words only)
  function linkChips(items, cls) {
    const ul = el("ul", "gl-chips gl-chips-go" + (cls ? " " + cls : ""));
    items.forEach((c) => {
      const li = el("li");
      if (c.href) {
        const a = el("a", "", c.name);
        a.href = c.href;
        if (c.title) a.title = c.title;
        li.className = "gl-chip-go";
        li.append(a);
      } else {
        li.textContent = c.name;
      }
      ul.append(li);
    });
    return ul;
  }

  // a series' genres — searches: «Видео → Лента» with the genre (its name in the feed — /api/catalog/filters), a
  // genre the feed has not — (v5.44.148) the feed's search by the word (a series of a site: «Лента», not the folder),
  // a name the program makes no search of — words only; the words at once, the links when the program has said
  function genreChips(names, cls) {
    const list = (names || []).filter(Boolean).map(String);
    const ul = chips(list, cls);
    getJson("/api/catalog/filters?kind=video" + list.map((n) => "&n=" + encodeURIComponent(n)).join(""))
      .then((reply) => {
        const items = (reply.chips || []).map((c) => ({
          name: c.name, title: c.g ? "Найти в «Ленте»" : c.never ? "" : "Искать в «Ленте» по слову",
          href: c.never ? "" : c.g ? "/videos/feed?g=" + encodeURIComponent(c.g) : "/videos/feed?q=" + encodeURIComponent(c.name),
        }));
        if (items.length && ul.isConnected) ul.replaceWith(linkChips(items, cls));
      }).catch(() => { /* the words stay */ });
    return ul;
  }

  // a gallery's tag — nhentai's galleries of it in «Манга → Лента»: (v5.44.148) its own tag chosen there
  // («sg=nhentai:artist:name»), not words in the search field
  function nhentaiSearch(type, name) {
    return "/manga/feed?sg=" + encodeURIComponent("nhentai:" + type + ":" + String(name).toLowerCase());
  }

  // a few lines of the description; «Ещё» — all of it
  function about(text) {
    const wrap = el("div", "gl-desc");
    const p = el("p", "gl-desc-text", text);
    wrap.append(p);
    const more = button("gl-link", "Ещё", () => {
      const open = wrap.classList.toggle("open");
      more.textContent = open ? "Свернуть" : "Ещё";
    });
    more.hidden = true;
    wrap.append(more);
    requestAnimationFrame(() => { more.hidden = p.scrollHeight <= p.clientHeight + 2; });
    return wrap;
  }

  function check(label, on) {
    const row = el("label", "gl-check");
    const tick = el("input");
    tick.type = "checkbox";
    tick.checked = !!on;
    row.append(tick, el("span", "", label));
    return { node: row, tick };
  }

  function legend(items) {
    const ul = el("ul", "gl-legend");
    items.forEach(([cls, text]) => {
      const li = el("li");
      li.append(el("span", "gl-swatch " + cls), document.createTextNode(text));
      ul.append(li);
    });
    return ul;
  }

  // the grid's tools: «Отметить: все · ничего», the count, «Скачать отмеченные» while the grid is scrolled
  function gridHead(title, count, tools) {
    const head = el("div", "gl-grid-head");
    const h = el("h3", "", title);
    if (count) h.append(" ", el("span", "gl-count", String(count)));
    head.append(h);
    tools.forEach((x) => { if (x) head.append(x); });
    return head;
  }

  function quick(picks, onPick) {
    const span = el("span", "gl-quick");
    span.setAttribute("role", "group");
    span.setAttribute("aria-label", "Отметить");
    span.append(el("span", "gl-quick-label", "Отметить:"));
    picks.forEach(([value, text]) => span.append(button("gl-link", text, () => onPick(value))));
    return span;
  }

  function paintTile(tick) {
    const cell = tick.closest(".gl-cell");
    if (cell) { cell.classList.toggle("on", tick.checked); cell.classList.toggle("off", tick.disabled); }
  }

  // ── the videos (video_get: the other chat's — its answers only read) ─────────────────────────────────────────
  const HAVE = { whole: "есть", part: "докачается", other: "свой файл" };
  const DUB_KINDS = [["dub", "Дубляж"], ["voice", "Озвучка"], ["subs", "Субтитры"]];
  const isSubs = (name) => /^субтитры/i.test(name || "");
  const dubKind = (name) => (/^дубляж/i.test(name || "") ? "dub" : isSubs(name) ? "subs" : "voice");
  const shortName = (name) => String(name || "").replace(/^(озвучка|дубляж|субтитры)\s+/i, "") || name || "—";

  function showVideo(st) {
    const f = st.found;
    const findId = st.id;
    const facts = f.facts || {};
    showMark("Видео");
    const view = hero(f.poster ? "/api/videos/find-poster?find=" + encodeURIComponent(findId) : "", f.title,
      ["Видео", facts.year, facts.type, facts.episodes ? "серий: " + facts.episodes : ""], (facts.alt || []).join(" / "));
    card.append(view.top);
    const a = view.about;
    if ((facts.genres || []).length) a.append(genreChips(facts.genres, "gl-genres"));     // (v5.44.130) searches
    if (facts.about) a.append(about(facts.about));
    (f.sources || []).forEach((x) => {
      const line = el("p", "gl-note" + (x.exact ? "" : " warn"));
      line.append(el("b", "", (x.dub || "Серии — с " + x.site) + ": "), x.what + " — " + x.how + "."
        + (x.ongoing ? " Тайтл ещё выходит: новые серии докачаются, если потом снова найти и скачать." : ""));
      a.append(line);
      (x.borrowed || []).forEach((other) => {
        const warn = el("p", "gl-note warn");
        warn.append(el("b", "", "Не этого тайтла: "), x.site + " приложил к этой странице видео тайтла «" + other.title
          + "» (" + other.videos + " шт.) — их здесь нет, чтобы под этим именем не скачались чужие серии. ");
        if (other.address) warn.append(button("gl-link", "Искать серии «" + other.title + "»", () => findOther(other.address)));
        a.append(warn);
      });
      const order = x.order || [];
      if (order.length > 1) {
        const block = el("details", "gl-order");
        block.append(el("summary", "", "Порядок просмотра на " + x.site + " · " + plural(order.length, ["часть", "части", "частей"])));
        const ol = el("ol");
        order.forEach((o) => {
          const li = el("li", o.here ? "here" : o.soon ? "soon" : "");
          const info = [o.type, o.year, o.kind, o.soon ? "анонс" : ""].filter(Boolean).join(" · ");
          if (o.here || o.soon || !o.address) li.append(el("span", "", o.title + (o.here ? " — эта страница" : "")));
          else li.append(button("gl-link", o.title, () => findOther(o.address)));
          if (info) li.append(el("span", "gl-muted", " " + info));
          ol.append(li);
        });
        block.append(ol);
        a.append(block);
      }
    });
    // (v5.44.152) the voice-overs only in players the program takes nothing from: by player — why, and where they
    // can be watched (the title's page of the site: the window opens it in the usual browser)
    if (f.choose === "dubs") {
      (f.closed || []).forEach((c) => {
        const line = el("p", "gl-note gl-closed", "Только в плеере " + c.player + ": " + c.names.join(", ") + ". " + c.why + ".");
        if (/^https?:\/\//.test(f.page_url || "")) {
          const go = el("a", "gl-closed-go", "Смотреть на сайте");
          go.href = f.page_url;
          go.target = "_blank";
          go.rel = "noopener noreferrer";
          line.append(" ", go);
        }
        a.append(line);
      });
    }
    const notes = [];
    if (f.episodes) notes.push("Серии сайт показывает на своих страницах — прочитано страниц серий: " + f.episodes + ".");
    if (f.streams) notes.push("Ещё на странице видео потоком DASH (.mpd): такие программа не скачивает.");
    if (f.hidden) notes.push("Часть ссылок плеер прячет (зашифрованы или вместо них заглушки): такие программа не скачивает.");
    if (f.frames && f.frames.length) notes.push("На странице есть плеер другого сайта (" + f.frames.join(", ") + "): оттуда программа не скачивает.");
    notes.forEach((text) => a.append(el("p", "gl-note", text)));

    const actions = el("div", "gl-actions");
    const goButton = iconButton("gl-btn primary", ICON.down);
    const go = goButton.node;
    actions.append(go);                // (v5.44.89) no way to the page of before: «Скачать» is the menu's tab alone
    // (v5.44.106) a series of YummyAnime, (v5.44.109) of Hentasis: «Смотреть» — watched without downloading (the
    // program says which links it shows: /api/watch/can); (v5.44.116) «Смотреть онлайн» — the phone's words too
    fetch("/api/watch/can?url=" + encodeURIComponent(f.page_url || ""), { credentials: "same-origin" })
      .then((r) => r.json()).then((can) => {
        if (!can || !can.watch || actions.querySelector(".gl-watch")) return;
        const watch = el("a", "gl-btn gl-watch", "Смотреть онлайн");
        watch.href = can.href;
        actions.append(watch);
      }).catch(() => { /* no «Смотреть» — «Скачать» is there */ });
    a.append(actions);
    const sum = el("p", "gl-sum");
    a.append(sum);

    const pick = f.choose === "dubs" ? dubsPicker(f, count, findId) : filesPicker(f, count);
    card.append(pick.node);

    function count() {
      const files = pick.chosen();
      const job = jobs.video;
      const other = !!(job && RUNNING.includes(job.status) && String(job.find) !== String(findId));
      const said = f.choose === "dubs" || files.some((i) => f.files[i].kind !== "video")
        ? plural(files.length, ["файл", "файла", "файлов"]) : plural(files.length, ["серию", "серии", "серий"]);
      // (v5.44.150) nothing ticked, «Заменить картинку у серий в папке» is: the pictures alone are downloaded
      const again = pick.repicture ? pick.repicture() : 0;
      go.disabled = !files.length && !again;
      goButton.words.textContent = files.length ? (other ? "В очередь: " : "Скачать ") + said
        : again ? (other ? "В очередь: " : "") + "Заменить картинку у " + plural(again, ["серии", "серий", "серий"])
          : pick.empty();
      pick.go.textContent = goButton.words.textContent;
      pick.go.hidden = !files.length && !again;
      let bytes = pick.weight(files);
      let approx = bytes > 0;
      if (!bytes) {
        files.forEach((i) => { bytes += Number(f.files[i].size) || 0; approx = approx || !!f.files[i].stream; });
      }
      sum.textContent = ((files.length || again) && bytes ? (approx ? "≈ " : "") + megabytes(bytes) + ", " : "")
        + "в папку «" + folderName(f.folder) + "» — " + (f.new_folder ? "новая" : "уже есть, новые серии лягут в неё")
        + (other ? " · начнётся после текущего скачивания" : "");
    }

    async function download() {
      const files = pick.chosen();
      const again = pick.repicture ? pick.repicture() : 0;
      if (!files.length && !again) return;
      go.disabled = true;
      try {
        const reply = await postJson(KINDS.video.get, { find: findId, files, quality: pick.quality(), merge: pick.merge(),
          repicture: !!again });
        LB.say(null, reply.queued ? (reply.queued > 1 ? "В очереди " + reply.queued + "-м" : "В очереди следующим")
          + ": начнётся само" : "Скачивание началось — оно в «Загрузках»");
        watch("video", true);
      } catch (err) {
        LB.say(null, err.message, true);
      }
      count();
    }
    go.addEventListener("click", download);
    pick.go.addEventListener("click", download);
    count();
    shown.count = count;
    shown.url = f.page_url || "";
    shown.poster = !!f.poster;
  }

  function findOther(address) {
    field.value = address;
    showMark("Видео");
    keepField(address);
    hideCard();
    findLink("video", address, "", "");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function episodeTile(text, mark, title) {
    const cell = el("div", "gl-cell");
    const label = el("label", "gl-tile");
    const tick = el("input", "gl-vh");
    tick.type = "checkbox";
    label.append(tick, el("span", "gl-tile-icon"), el("span", "gl-tile-n", text || "•"), el("span", "gl-tile-fill"));
    const note = el("span", "gl-tile-mark", mark || "");
    label.append(note);
    cell.append(label);
    if (title) cell.title = title;
    return { cell, tick, note };
  }

  // the episodes of a page as tiles: the new ones ticked
  function filesPicker(f, changed) {
    const node = el("section", "gl-pick");
    const groups = new Map();
    f.files.forEach((x) => {
      const key = x.kind === "audio" ? "\u0000" : x.group || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(x);
    });
    const keys = [...groups.keys()].sort((a, b) => (a === "\u0000") - (b === "\u0000"));
    const go = button("gl-btn small", "");
    go.hidden = true;
    node.append(gridHead(f.files.every((x) => x.kind === "audio") ? "Файлы" : "Серии", f.files.length,
      [quick([["new", "новые"], ["all", "все"], ["none", "ничего"]], (how) => {
        boxes().forEach((t) => {
          if (t.disabled) return;
          t.checked = how === "all" || (how === "new" && t.dataset.kind === "video");
          paintTile(t);
        });
        changed();
      }), go, legend([["have", "есть в папке"], ["on", "отмечена"]])]));
    keys.forEach((key) => {
      if (keys.length > 1 || key) node.append(el("h4", "gl-group", key === "\u0000" ? "Звук" : key || "Серии"));
      const grid = el("div", "gl-grid");
      groups.get(key).forEach((x) => {
        const said = x.blocked && !x.have ? "нет" : x.have === "whole" ? "" : HAVE[x.have] || x.quality || "";
        const t = episodeTile(x.kind === "audio" ? "♪" : x.label, said, [x.title, x.note, (x.group ? x.group + " / " : "") + x.name,
          x.size ? (x.stream ? "≈ " : "") + megabytes(x.size) : "", x.blocked ? "Не скачать: " + x.blocked : ""].filter(Boolean).join("\n"));
        t.tick.value = String(x.i);
        t.tick.dataset.kind = x.kind;
        t.tick.disabled = x.have === "whole" || x.have === "other" || !!x.blocked;
        t.tick.checked = !t.tick.disabled && x.kind === "video";
        t.cell.dataset.state = x.have === "whole" || x.have === "other" ? "have" : x.blocked ? "wait" : "";
        if (t.cell.dataset.state === "have") t.cell.querySelector(".gl-tile-icon").append(svg(ICON.check, 12));
        t.tick.setAttribute("aria-label", (x.kind === "audio" ? "Звук " + (x.title || x.name) : "Серия " + (x.label || ""))
          + (said ? ", " + said : ""));
        paintTile(t.tick);
        grid.append(t.cell);
      });
      node.append(grid);
    });
    node.addEventListener("change", (event) => {
      if (event.target.matches("input[type='checkbox']")) { paintTile(event.target); changed(); }
    });
    function boxes() { return [...node.querySelectorAll(".gl-grid input[type='checkbox']")]; }
    return {
      node, go,
      chosen: () => boxes().filter((t) => t.checked).map((t) => Number(t.value)),
      empty: () => (boxes().some((t) => !t.disabled) ? "Отметьте серии" : "Всё уже скачано"),
      quality: () => "", merge: () => false, weight: () => 0,
    };
  }

  // every voice-over of a title: the kind («Дубляж», «Озвучка», «Субтитры»), its studios, the quality — and the
  // episodes; what is downloaded is each episode ticked of each studio ticked, not what is there already
  function dubsPicker(f, changed, findId) {
    const node = el("section", "gl-pick gl-dubs");
    const groups = new Map();
    f.files.forEach((x) => {
      const name = x.group || "";
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(x);
    });
    const names = [...groups.keys()];
    const chosen = new Set(names.filter((n) => groups.get(n).some((x) => x.have === "whole" || x.have === "part")));
    const episodes = new Set();
    const present = DUB_KINDS.filter(([k]) => names.some((n) => dubKind(n) === k));
    const begun = [...chosen][0];
    let kind = begun !== undefined ? dubKind(begun) : ((present.find(([k]) => k === "voice") || present[0] || ["voice"])[0]);
    let measured = null;

    node.append(el("h3", "gl-h", "Что скачать"));
    const tabs = el("div", "gl-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Вид");
    present.forEach(([k, title]) => {
      const b = button("", "");
      b.setAttribute("role", "tab");
      b.dataset.kind = k;
      b.append(document.createTextNode(title + " "), el("span", "gl-count", ""));
      b.addEventListener("click", () => { kind = k; paint(); });
      tabs.append(b);
    });
    node.append(tabs);
    const studios = el("ul", "gl-studios");
    node.append(studios);
    const subsNote = el("p", "gl-note", "Слова субтитров вшиты в картинку: каждый релиз — отдельное видео, весит как серия "
      + "целиком и в общий файл серии не собирается.");
    node.append(subsNote);
    const row = el("div", "gl-opts gl-opts-row");
    const qualityBox = el("label", "gl-pick-select");
    const quality = el("select");
    [["best", "лучшее"], ["1080", "не выше 1080p"], ["720", "не выше 720p"], ["480", "не выше 480p"],
      ["360", "не выше 360p"]].forEach(([v, t]) => {
      const option = el("option", "", t);
      option.value = v;
      quality.append(option);
    });
    quality.value = kept("lb.get-quality", "best");
    if (!quality.value) quality.value = "best";
    quality.addEventListener("change", () => { keep("lb.get-quality", quality.value); changed(); });
    qualityBox.append(el("span", "", "Качество"), quality);
    const merge = check("Собрать озвучки серии в один файл", box.dataset.merge === "1");
    merge.tick.addEventListener("change", () => { ask(); changed(); });
    quality.addEventListener("change", () => ask());
    row.append(qualityBox, merge.node);
    node.append(row);
    // (v5.44.150; the owner: «удалило весь видеоряд а оставило jam где всего 720p») what the picture of an episode
    // will be: the title's best one — said before «Скачать», with the release it comes from (one not ticked lends
    // its picture alone); the episodes of the folder with a lower one — «Заменить картинку…» (off until ticked)
    const pictureNote = el("p", "gl-note gl-picture");
    pictureNote.hidden = true;
    const again = check("", false);
    again.node.classList.add("gl-repicture");
    again.node.hidden = true;
    again.tick.addEventListener("change", changed);
    node.append(pictureNote, again.node);
    let known = { releases: {}, picture: null, lower: { episodes: 0, height: 0 } };
    const go = button("gl-btn small", "");
    go.hidden = true;
    const counted = el("span", "gl-counted");
    const labels = [...new Set(f.files.map((x) => x.label || ""))].sort(natural);
    node.append(gridHead("Серии", labels.length, [counted, quick([["all", "все"], ["none", "ничего"]], (how) => {
      grid.querySelectorAll("input").forEach((t) => {
        if (!t.disabled) t.checked = how === "all";
        if (t.checked) episodes.add(t.value); else episodes.delete(t.value);
      });
      paint();
    }), go, legend([["have", "есть у выбранных"], ["part", "есть частично"], ["on", "отмечена"]])]));
    const grid = el("div", "gl-grid");
    labels.forEach((label) => {
      const t = episodeTile(label, "", "Серия " + label);
      t.tick.value = label;
      t.cell.dataset.label = label;
      grid.append(t.cell);
    });
    node.append(grid);
    node.addEventListener("change", (event) => {
      const t = event.target;
      if (t.closest(".gl-studios")) {
        if (t.checked) chosen.add(t.value); else chosen.delete(t.value);
        ask();                                   // (v5.44.150) another release ticked: the picture may be another's
      } else if (t.closest(".gl-grid")) {
        if (t.checked) episodes.add(t.value); else episodes.delete(t.value);
      } else {
        return;
      }
      paint();
    });

    function facts(list, name) {
      let best = 0;
      const players = [];
      list.forEach((x) => {
        best = Math.max(best, parseInt(String(x.quality || "").replace(/\D+/g, ""), 10) || 0);
        String(x.note || "").replace("Плеер: ", "").split(", ").forEach((p) => { if (p && !players.includes(p)) players.push(p); });
      });
      // (v5.44.150) what the release really has, once its files were looked at (CVH's «до 1080p» was the player's
      // best — AniLibria's files there end at 720p), and whether its cut is the original's
      const real = known.releases[name];
      const size = real && real.known ? real.height + "p" : best ? "до " + best + "p" : "";
      const cut = !real || real.clean === null || real.clean === undefined ? ""
        : real.clean ? "без перемонтажа" : "перемонтаж студии";
      return [size, cut, players.join(", ")].filter(Boolean).join(" · ");
    }

    // which voice-overs are ticked (not the subtitles' releases: each of them is a video of its own)
    const voices = () => [...chosen].filter((n) => !isSubs(n));

    function paintPicture() {
      const p = known.picture;
      const on = merge.tick.checked && p && p.group && voices().length > 0;
      pictureNote.hidden = !on;
      if (on) {
        const cut = p.clean === true ? ", без перемонтажа" : p.clean === false ? ", перемонтаж студии" : "";
        pictureNote.textContent = "Картинка серий — от «" + p.title + "»: " + p.height + "p" + cut
          + (p.lent ? ". Её озвучка не отмечена — скачается только ради картинки, её звук в файл серии не пойдёт." : ".");
      }
      const lower = known.lower || {};
      const can = on && lower.episodes > 0;
      again.node.hidden = !can;
      if (!can) again.tick.checked = false;
      else {
        again.node.querySelector("span").textContent = "Заменить картинку у "
          + plural(lower.episodes, ["серии", "серий", "серий"]) + " в папке: сейчас до " + lower.height + "p, станет "
          + p.height + "p";
      }
    }

    // asked again when the ticks that decide the picture change (the releases are looked at once: quick after it)
    let asking = 0;
    let askTimer = 0;
    function ask() {
      clearTimeout(askTimer);
      askTimer = setTimeout(() => {
        const mine = ++asking;
        const query = ["find=" + encodeURIComponent(findId), "quality=" + encodeURIComponent(quality.value)]
          .concat(merge.tick.checked ? voices().map((n) => "g=" + encodeURIComponent(n)) : []);
        getJson("/api/videos/measure?" + query.join("&")).then((reply) => {
          if (mine !== asking) return;
          const first = measured === null;
          measured = reply.measured || {};
          known = { releases: reply.releases || {}, picture: reply.picture || null,
            lower: reply.lower || { episodes: 0, height: 0 } };
          if (first || Object.keys(known.releases).length) paint(); else { paintPicture(); changed(); }
        }).catch(() => { if (mine === asking) { measured = measured || {}; changed(); } });
      }, measured === null ? 0 : 250);
    }

    function paint() {
      tabs.querySelectorAll("[role='tab']").forEach((b) => {
        const mine = names.filter((n) => dubKind(n) === b.dataset.kind);
        const on = mine.filter((n) => chosen.has(n)).length;
        b.setAttribute("aria-selected", b.dataset.kind === kind ? "true" : "false");
        b.querySelector(".gl-count").textContent = on ? on + " из " + mine.length : String(mine.length);
      });
      studios.textContent = "";
      names.filter((n) => dubKind(n) === kind).forEach((name) => {
        const list = groups.get(name);
        const had = list.filter((x) => x.have === "whole").length;
        const li = el("li");
        const label = el("label", "gl-studio");
        const tick = el("input");
        tick.type = "checkbox";
        tick.value = name;
        tick.checked = chosen.has(name);
        const text = el("span", "gl-studio-text");
        text.append(el("b", "", shortName(name)), el("span", "gl-muted", [facts(list, name),
          (had ? "есть " + had + " из " : "") + plural(list.length, ["серия", "серии", "серий"])].filter(Boolean).join(" · ")));
        label.append(tick, text);
        label.title = name;
        li.append(label);
        studios.append(li);
      });
      subsNote.hidden = kind !== "subs";
      const some = chosen.size > 0;
      let ticked = 0;
      grid.querySelectorAll(".gl-cell").forEach((cell) => {
        const label = cell.dataset.label;
        const files = f.files.filter((x) => (x.label || "") === label && chosen.has(x.group || ""));
        const had = files.filter((x) => x.have === "whole" || x.have === "other").length;
        const tick = cell.querySelector("input");
        tick.disabled = !files.length || had === files.length;
        tick.checked = !tick.disabled && episodes.has(label);
        if (tick.checked) ticked += 1;
        cell.dataset.state = files.length && had === files.length ? "have" : had ? "part" : !files.length && some ? "wait" : "";
        const icon = cell.querySelector(".gl-tile-icon");
        icon.textContent = "";
        if (cell.dataset.state === "have") icon.append(svg(ICON.check, 12));
        cell.querySelector(".gl-tile-mark").textContent = !some ? "" : !files.length ? "нет" : had === files.length ? ""
          : had ? had + "/" + files.length : "";
        tick.setAttribute("aria-label", "Серия " + label + (cell.dataset.state === "have" ? ", есть" : ""));
        paintTile(tick);
      });
      counted.textContent = some ? "отмечено " + ticked + " из " + labels.length : "";
      paintPicture();
      changed();
    }

    function picked() {
      return f.files.filter((x) => episodes.has(x.label || "") && chosen.has(x.group || "") && x.have !== "whole"
        && x.have !== "other" && !x.blocked).map((x) => x.i);
    }

    // what the chosen ones take on the disk: one episode measured in each quality (/api/videos/measure), as the
    // computer's page of it counts (videos.js estimate)
    function weightOf(files) {
      const m = measured;
      if (!m || !m.sizes || !m.seconds) return 0;
      const heights = Object.keys(m.sizes).map(Number).sort((a, b) => b - a);
      const cap = quality.value === "best" ? 1e9 : Number(quality.value) || 1e9;
      const sizeAt = (limit) => {
        const h = heights.find((x) => x <= Math.min(cap, limit)) || heights[heights.length - 1];
        return m.sizes[String(h)] || 0;
      };
      const limitOf = (x) => {
        const real = known.releases[x.group || ""];
        return real && real.known ? real.height
          : /CVH/.test(x.note || "") ? 1e9 : /AniLiberty/.test(x.note || "") ? 1080 : 720;
      };
      const sound = 14000 * m.seconds;
      const lent = merge.tick.checked && known.picture && known.picture.lent ? known.picture : null;
      let total = 0;
      const byEpisode = new Map();
      files.forEach((i) => {
        const x = f.files[i];
        if (!byEpisode.has(x.label)) byEpisode.set(x.label, []);
        byEpisode.get(x.label).push(x);
      });
      byEpisode.forEach((mine, label) => {
        const voices = mine.filter((x) => !isSubs(x.group));
        total += mine.filter((x) => isSubs(x.group)).reduce((s, x) => s + sizeAt(limitOf(x)), 0);
        if (!voices.length) return;
        if (!merge.tick.checked) { total += voices.reduce((s, x) => s + sizeAt(limitOf(x)), 0); return; }
        const had = f.files.some((x) => x.label === label && !isSubs(x.group) && x.have === "whole");
        // a picture lent by a release not ticked: its picture and every ticked one's sound
        if (lent && !had) { total += sizeAt(lent.height) + voices.length * sound; return; }
        total += (had ? 0 : sizeAt(Math.max(...voices.map(limitOf)))) + (voices.length - (had ? 0 : 1)) * sound;
      });
      return total + repicture() * sizeAt(known.picture ? known.picture.height : 1e9);
    }

    // «Заменить картинку у N серий в папке»: how many (0 — not ticked, or there is none to replace)
    function repicture() {
      return again.tick.checked && !again.node.hidden ? Number((known.lower || {}).episodes) || 0 : 0;
    }
    ask();
    setTimeout(paint, 0);

    return {
      node, go, repicture,
      weight: (files) => (files.length || repicture() ? weightOf(files) : 0),
      chosen: picked,
      empty: () => (chosen.size ? "Отметьте серии" : "Выберите озвучку"),
      quality: () => quality.value,
      merge: () => merge.tick.checked,
    };
  }

  // ── nhentai ───────────────────────────────────────────────────────────────────────────────────────────────────
  // (v5.44.97) one gallery — its card: the cover, the titles, the tags, the thumbnails of its pages (asked of the
  // program: /api/manga/gallery — the API of the site; a tile of «Манга → Лента» leads here); «Скачать» stays when the
  // description does not come; a gallery the program does not open — said, nothing of it shown
  function showNhentai(ids) {
    const gen = ++nhGen;
    card.textContent = "";
    card.dataset.kind = "manga";
    shown = { kind: "manga", id: 0 };
    const one = ids.length === 1;
    const view = hero("", one ? "Галерея nhentai " + ids[0] : "Галереи nhentai: " + ids.length, ["Хентай", "nhentai"], "");
    card.append(view.top);
    if (!one) {
      // (v5.44.130) a number — its gallery's page
      view.about.append(linkChips(ids.slice(0, 40).map((n) => ({
        name: "№ " + n, title: "Страница галереи", href: "/manga/title?url="
          + encodeURIComponent("https://nhentai.net/g/" + n + "/") })), "gl-numbers"));
    }
    const note = el("p", "gl-note", "Галерея ляжет в папку манги со своими названиями и тегами; прерванная "
      + "докачается. Обложка и название появятся в «Загрузках», когда программа прочитает описание.");
    view.about.append(note);
    const actions = el("div", "gl-actions");
    const go = iconButton("gl-btn primary", ICON.down, one ? "Скачать"
      : "Скачать " + plural(ids.length, ["галерею", "галереи", "галерей"])).node;
    actions.append(go);
    view.about.append(actions);
    card.hidden = false;
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        const reply = await postJson(KINDS.manga.get, { text: ids.join(" ") });
        // (v5.44.107) a download going: they wait in it — the queue
        LB.say(null, reply.already ? "Уже в «Загрузках» — качается или скачана"
          : reply.queued ? (reply.queued > 1 ? "В очереди " + reply.queued + "-й" : "В очереди следующей")
            + " — в «Загрузках»" : "Скачивание началось — оно в «Загрузках»");
        watch("manga", true);
      } catch (err) {
        LB.say(null, err.message, true);
      }
      go.disabled = false;
    });
    if (one) describeGallery(ids[0], gen, view, note, actions, go);
  }

  async function describeGallery(id, gen, view, note, actions, go) {
    card.classList.add("gl-busy");
    let g = null;
    try {
      const response = await fetch("/api/manga/gallery?id=" + encodeURIComponent(id),
        { credentials: "same-origin", cache: "no-store" });
      g = await response.json();
    } catch (_) {
      g = { ok: false, error: "Программа не отвечает" };
    }
    if (gen !== nhGen) return;
    card.classList.remove("gl-busy");
    if (!g || !g.ok) {
      if (g && g.forbidden) {                    // not opened by the program: no picture, no name, no «Скачать»
        card.textContent = "";
        const top = hero("", "Галерея nhentai " + id, ["Хентай", "nhentai"], "");
        top.about.append(el("p", "gl-note warn", g.error));
        card.append(top.top);
        return;
      }
      note.textContent = "Описание не пришло: " + ((g && g.error) || "сайт не ответил") + ". Скачать можно и так.";
      return;
    }
    const facts = ["Хентай", "nhentai", plural(g.pages, ["страница", "страницы", "страниц"]), g.date];
    const fresh = hero(g.cover ? "/api/catalog/cover?c=" + encodeURIComponent(g.cover) : "", g.title, facts,
      g.japanese && g.japanese !== g.title ? g.japanese : "");
    card.textContent = "";
    card.append(fresh.top);
    const a = fresh.about;
    if (g.full && g.full !== g.title) a.append(el("p", "gl-alt gl-nh-full", g.full));
    const row = el("div", "gl-row");
    row.append(el("span", "gl-fact", "№ " + g.id));
    if (g.favorites) row.append(el("span", "gl-fact", "в избранном у " + g.favorites));
    a.append(row);
    (g.tags || []).forEach((group) => {
      const line = el("div", "gl-nh-tags");
      line.append(el("span", "gl-nh-kind", group.kind));
      // (v5.44.130) a tag, an artist… — nhentai's search in «Лента»
      line.append(linkChips(group.names.slice(0, 40).map((name) => ({ name, title: "Найти в «Ленте»",
        href: group.type ? nhentaiSearch(group.type, name) : "" })), "gl-genres"));
      a.append(line);
    });
    if (g.have) {
      // (v5.44.116) all its pages there: «✓ В папке» — «Скачать» would download nothing; cut short — «Докачать N»
      // (the pages a download asks for: ``have.missing``, as it counts them)
      const missing = g.have.missing !== undefined ? Number(g.have.missing) || 0 : Math.max(0, g.pages - g.have.pages);
      const words = go.querySelector("span");
      if (words) words.textContent = missing ? "Докачать " + plural(missing, ["страницу", "страницы", "страниц"])
        : "✓ В папке";
      if (!missing) {
        go.disabled = true;
        go.querySelectorAll("svg").forEach((icon) => icon.remove());
      }
      const there = el("p", "gl-note", "");
      there.append("Уже в папке манги: ", el("b", "", g.have.folder),
        !missing ? " — все страницы на месте." : " — страниц там " + g.have.pages + " из " + g.pages
          + ", «Докачать» скачает остальные.");
      a.append(there);
      if (g.have.open) {
        const open = el("a", "gl-btn", "");
        open.href = g.have.open;
        open.append(svg(ICON.open, 16), el("span", "", "Открыть"));
        actions.append(open);
      }
    } else {
      a.append(el("p", "gl-note", "Галерея ляжет в папку манги со своими названиями и тегами; прерванная докачается."));
    }
    a.append(actions);
    if ((g.thumbs || []).length) card.append(pagesStrip(g.thumbs));
  }

  // the gallery's pages as thumbnails: the first rows, «Все страницы» — the rest
  function pagesStrip(thumbs) {
    const SHOWN = 24;
    const box = el("section", "gl-nh-pages");
    box.append(el("h3", "gl-h", "Страницы"));
    const grid = el("div", "gl-nh-grid");
    const add = (from, to) => thumbs.slice(from, to).forEach((c, k) => {
      const cell = el("figure", "gl-nh-page");
      const img = el("img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.onerror = () => cell.classList.add("gl-nh-missing");
      img.src = "/api/catalog/cover?c=" + encodeURIComponent(c);
      cell.append(img, el("figcaption", "", String(from + k + 1)));
      grid.append(cell);
    });
    add(0, SHOWN);
    box.append(grid);
    if (thumbs.length > SHOWN) {
      const more = button("gl-link", "Все страницы (" + thumbs.length + ")", () => {
        add(SHOWN, thumbs.length);
        more.remove();
      });
      box.append(more);
    }
    return box;
  }

  // ── «Найти» by a name: the sites' searches at once, the titles as covers (manhwa_search) ─────────────────────────
  const GROUPS = [["manga", "Манга"], ["strip", "Манхва, маньхуа и другие"]];
  let hitsId = 0;
  let hitsTimer = 0;
  let hitsQuery = "";
  let hitsCount = 0;
  let picked = "";
  const hitTiles = new Map();

  async function searchName(text) {
    clearTimeout(hitsTimer);
    hideCard();
    findButton.disabled = true;
    try {
      const reply = await postJson("/api/manhwa/search", { q: text });
      startHits(reply.id, text);
      pollHits();
    } catch (err) {
      tellError(err);
    }
    findButton.disabled = false;
  }

  function startHits(id, text) {
    hitsId = id;
    hitsQuery = text;
    hitsCount = 0;
    picked = "";
    hitTiles.clear();
    hitsList.textContent = "";
    hitsSites.textContent = "";
    hitsBox.classList.remove("folded");
    hitsBox.classList.add("busy");
    hitsBox.hidden = false;
    hitsFold.hidden = true;
    hitsTitle.textContent = "Ищу «" + text + "»…";
  }

  async function pollHits() {
    clearTimeout(hitsTimer);
    const asked = hitsId;
    try {
      const st = await getJson("/api/manhwa/search-state?id=" + asked);
      if (asked !== hitsId) return;
      showHits(st);
      if (!st.done) hitsTimer = setTimeout(pollHits, 500);
    } catch (_) {
      if (asked === hitsId) hitsTimer = setTimeout(pollHits, 2000);
    }
  }

  function dropHits() {
    clearTimeout(hitsTimer);
    hitsId = 0;
    hitsBox.hidden = true;
  }

  function showHits(st) {
    const titles = st.titles || [];
    hitsCount = st.count || titles.length;
    hitsBox.classList.toggle("busy", !st.done);
    hitsTitle.textContent = !st.done ? "Ищу «" + hitsQuery + "»…" + (hitsCount ? " Пока " + plural(hitsCount, TITLES) : "")
      : hitsCount ? "По названию «" + hitsQuery + "» — " + plural(hitsCount, TITLES)
        : "По названию «" + hitsQuery + "» ничего не нашлось — попробуйте другое название (по-английски, по-русски) или ссылку";
    hitsSites.textContent = "";
    (st.sites || []).forEach((x) => {
      const said = x.state === "asking" ? "…" : x.state === "failed" ? "не ответил" : String(x.count);
      const item = el("li", "gl-hits-site " + x.state);
      item.append(el("b", "", x.label), document.createTextNode(" " + said));
      if (x.error) item.title = x.error;
      hitsSites.append(item);
    });
    const seen = new Set();
    const lists = {};
    GROUPS.forEach(([key, name]) => {
      let group = hitsList.querySelector('.gl-hits-group[data-group="' + key + '"]');
      if (!group) {
        group = el("section", "gl-hits-group");
        group.dataset.group = key;
        group.append(el("h3", "", name), el("div", "gl-hits-grid"));
        hitsList.append(group);
      }
      lists[key] = { group, grid: group.querySelector(".gl-hits-grid"), name, tiles: [] };
    });
    const first = {};
    titles.forEach((t, k) => {
      const key = t.kind === "manga" ? "manga" : "strip";
      lists[key].tiles.push(hitTile(t));
      if (!(key in first)) first[key] = k;
      seen.add(t.url);
    });
    hitTiles.forEach((tile, key) => { if (!seen.has(key)) { tile.remove(); hitTiles.delete(key); } });
    Object.values(lists).forEach((x) => {
      const now = [...x.grid.children];
      if (now.length !== x.tiles.length || now.some((tile, k) => tile !== x.tiles[k])) x.grid.append(...x.tiles);
      x.group.hidden = !x.tiles.length;
      x.group.querySelector("h3").textContent = x.name + " · " + x.tiles.length;
    });
    // the group of the best title first — the sites' order puts the very name first; «Манга» first always put a
    // manhwa searched by its own name under the mangas whose names only look like it
    const at = (key) => (key in first ? first[key] : 1e9);
    const order = GROUPS.map(([key]) => key).sort((a, b) => at(a) - at(b));
    if ([...hitsList.children].map((g) => g.dataset.group).join() !== order.join()) {
      order.forEach((key) => hitsList.append(lists[key].group));
    }
    if (picked && hitsBox.classList.contains("folded")) showFold();
  }

  function hitTile(t) {
    let tile = hitTiles.get(t.url);
    if (!tile) {
      tile = button("gl-hit");
      tile.dataset.url = t.url;
      const cover = el("span", "gl-hit-cover");
      const initial = el("span", "gl-initial", (t.title || "?").trim().charAt(0).toUpperCase());
      initial.setAttribute("aria-hidden", "true");
      cover.append(initial);
      tile.append(cover, el("span", "gl-hit-name"), el("span", "gl-hit-alt"), el("span", "gl-hit-facts"), el("span", "gl-hit-sites"));
      tile.addEventListener("click", () => pickHit(tile.dataset.url));
      hitTiles.set(t.url, tile);
    }
    tile.hit = t;
    const cover = tile.querySelector(".gl-hit-cover");
    if ((t.cover || "") !== (tile.dataset.cover || "")) {
      tile.dataset.cover = t.cover || "";
      cover.querySelectorAll("img").forEach((x) => x.remove());
      cover.classList.remove("has-picture");
      if (t.cover) {
        const img = el("img");
        img.alt = "";
        img.loading = "lazy";
        img.decoding = "async";
        img.onload = () => cover.classList.add("has-picture");
        img.onerror = () => img.remove();
        img.src = "/api/manhwa/hit-cover?s=" + encodeURIComponent(hitsId) + "&c=" + encodeURIComponent(t.cover);
        cover.append(img);
      }
    }
    cover.title = t.cover_hidden ? "Обложка скрыта (SFW)" : "";
    let badge = cover.querySelector(".gl-hit-badge");
    if (t.adult && !badge) cover.append(el("span", "gl-hit-badge", "18+"));
    else if (!t.adult && badge) badge.remove();
    tile.querySelector(".gl-hit-name").textContent = t.title || "Без названия";
    const alt = tile.querySelector(".gl-hit-alt");
    alt.textContent = (t.alt || [])[0] || "";
    alt.hidden = !alt.textContent;
    tile.title = [t.title, alt.textContent].filter(Boolean).join("\n");
    const facts = [t.type, t.year || "", t.chapters ? plural(t.chapters, CHAPTERS) : "",
      t.rating ? "★ " + String(t.rating).replace(".", ",") : ""].filter(Boolean);
    tile.querySelector(".gl-hit-facts").textContent = facts.join(" · ");
    tile.querySelector(".gl-hit-sites").textContent = (t.sites || []).map((x) => x.label).join(", ");
    const on = t.url === picked;
    tile.classList.toggle("picked", on);
    tile.setAttribute("aria-pressed", on ? "true" : "false");
    tile.setAttribute("aria-label", [t.title, ...facts, "на " + (t.sites || []).map((x) => x.label).join(", ")].filter(Boolean).join(", "));
    return tile;
  }

  function showFold() {
    hitsFold.hidden = false;
    hitsFold.querySelector("button").textContent = "Вернуться к найденному по «" + hitsQuery + "» — " + plural(hitsCount, TITLES);
  }

  function markPicked(url) {
    picked = url;
    hitTiles.forEach((x, key) => {
      x.classList.toggle("picked", key === url);
      x.setAttribute("aria-pressed", key === url ? "true" : "false");
    });
  }

  // back to the page (a title read, F5): the name searched before — folded over its card, as it was left
  // (v5.44.119) back to the page from a title picked here: the titles found by the name, the picked one marked (the
  // search let go meanwhile — searched anew)
  async function restoreHits(text, url) {
    if (/^https?:\/\//i.test(text)) return;
    try {
      const st = await getJson("/api/manhwa/search-state?id=last");
      const same = (x) => String(x || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (hitsId) return;
      if (!st.id || same(st.query) !== same(text)) { ask(text); return; }
      startHits(st.id, text);
      showHits(st);
      markPicked(url || "");
      if (!st.done) pollHits();
    } catch (_) {
      ask(text);
    }
  }

  // (v5.44.119) a title picked — its one page (what it is is known: its kind goes with it, its name and rating too)
  function pickHit(url) {
    const tile = hitTiles.get(url);
    if (!tile || !tile.hit) return;
    markPicked(url);
    openTitle(url, hitsQuery, tile.hit);
  }

  hitsFold.querySelector("button").addEventListener("click", () => {
    hitsBox.classList.remove("folded");
    hitsFold.hidden = true;
  });

  // ── the downloads going, as the card needs them (v5.44.120: «Загрузки» are a part of «Скачать» of their own —
  // downloads.py, downloads.js; here only what the cards use: «В очередь» while another download goes, the card read
  // again when its download has ended) ─────────────────────────────────────────────────────────────────────────────
  const jobs = {};               // kind -> its last state
  const polling = {};            // kind -> {gen, timer}: one loop of questions per kind, a newer one takes over
  const runs = { since: 0, run: 0 };    // the manhwa's chapters asked after this (its tiles on the card)

  function watch(kind, now) {
    const was = polling[kind];
    if (was && !now) return;
    if (was) clearTimeout(was.timer);
    const gen = (was ? was.gen : 0) + 1;
    polling[kind] = { gen, timer: 0 };
    if (now) tick(kind, gen); else polling[kind].timer = setTimeout(() => tick(kind, gen), 0);
  }

  const mineNow = (kind, gen) => !!polling[kind] && polling[kind].gen === gen;

  function stateAddress(kind) {
    if (kind !== "manhwa") return KINDS[kind].state;
    const job = jobs.manhwa;
    const mine = shown && shown.kind === "manhwa" && shown.tiles && job && String(job.find) === String(shown.id);
    return KINDS.manhwa.state + (mine ? "?since=" + runs.since + "&run=" + runs.run : "?items=none");
  }

  async function tick(kind, gen) {
    let st;
    try {
      st = await stateOf("job-" + kind, stateAddress(kind));
    } catch (_) {
      if (mineNow(kind, gen)) polling[kind].timer = setTimeout(() => tick(kind, gen), 5000);
      return;
    }
    if (!mineNow(kind, gen)) return;           // a newer loop has asked since
    const was = jobs[kind];
    jobs[kind] = st;
    if (kind === "manhwa") paintTiles(st);
    if (shown && shown.count && shown.kind === kind) shown.count();
    const goes = RUNNING.includes(st.status) || (!!KINDS[kind].unqueue && ((st.queue || []).length || st.restoring));
    if (goes) {
      polling[kind].timer = setTimeout(() => tick(kind, gen), document.hidden ? 6000 : 1500);
      return;
    }
    delete polling[kind];
    const ended = !!st.id && !!was && (RUNNING.includes(was.status) || was.id !== st.id);
    if (ended && shown && shown.kind === kind && kind !== "manga") refreshFound(kind);
  }

  function paintTiles(st) {
    const m = st.meter || {};
    if (!shown || shown.kind !== "manhwa" || !shown.tiles || String(st.find) !== String(shown.id)) return;
    if (m.run !== runs.run) { runs.run = m.run || 0; runs.since = 0; }
    if (Array.isArray(st.items) && st.items.length) shown.tiles.paintRun(st.items);
    runs.since = Math.max(runs.since, Number(m.rev) || 0);
  }

  async function refreshFound(kind) {
    const mine = shown;
    try {
      const st = await getJson(withAfter(findAddress(kind, mine.id), 999999999));
      if (shown === mine && st.found && st.id === mine.id) showFound(kind, st);
    } catch (_) { /* the card stays as it was */ }
  }

  // ── the page ──────────────────────────────────────────────────────────────────────────────────────────────────
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = field.value.trim();
    ask(text, !!shown && shown.text === text);      // the same card again — asked anew
  });
  field.addEventListener("paste", () => {
    setTimeout(() => {
      const text = field.value.trim();
      if (/^https?:\/\/\S+$/i.test(text) && !findButton.disabled) ask(text);
    }, 30);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) ORDER.forEach((kind) => watch(kind));
  });

  ORDER.forEach((kind) => watch(kind));
  if (field.value.trim()) markKind();
  if (box.dataset.autofind === "1" && field.value.trim()) {
    ask(field.value);
  } else if (field.value.trim()) {     // back to the page, a reload: the card of this link, if it is still there
    let last = null;
    try { last = JSON.parse(stored("gl-last") || "null"); } catch (_) { last = null; }
    const text = field.value.trim();
    const same = (x) => String(x || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (last && last.kind === "title" && same(last.text) === same(text)) {
      // (v5.44.119) back from a title's page: a link — the line to open it again; a name — the titles found by it
      if (/^https?:\/\//i.test(text)) titleLine(last.url || text);
      else restoreHits(text, last.url);
    } else if (last && last.text === text && KINDS[last.kind] && KINDS[last.kind].findState) {
      getJson(withAfter(findAddress(last.kind, last.id), 999999999)).then((st) => {
        if (st.gone || st.id !== last.id) { ask(text); return; }
        if (RUNNING.includes(st.status)) { finding = last; busy(true); waitFound(); }
        else if (st.found && !shown) showFound(last.kind, st);
        else if (!st.found) ask(text);
      }).catch(() => ask(text));
    } else {
      ask(text);
    }
  }
})();
