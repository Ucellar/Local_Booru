// Local Booru «Скачать → По ссылке» (v5.44.87, webui/pages/get_link.py): one field for every download.  A link or a
// name → what it is (/api/get/what: the mark in the field) → the card of what was found, from the program's own
// machinery by its addresses: the titles of the sites of manga (manhwa_get: «Найти», its chapters as tiles, «Читать»
// without downloading — /online, «Скачать»), the videos (video_get — the other chat's: its answers are only read, its
// download is its own) and nhentai (manga_get).  On the right — «Загрузки»: the downloads of the three going, their
// queues, «Пауза», «Остановить», the ones ended with «Открыть».  The phone's page of it — phone-get.js.
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
  const side = box.querySelector(".gl-side");
  const jobsList = side.querySelector(".gl-jobs");
  const sideCount = side.querySelector(".gl-side-count");
  const sideEmpty = side.querySelector(".gl-side-empty");
  const sideButtons = side.querySelector(".gl-side-buttons");
  const RUNNING = ["starting", "running", "stopping"];
  const KINDS = {
    video: { label: "Видео", find: "/api/videos/find", findFile: "/api/videos/find-file",
             findState: "/api/videos/find-state", get: "/api/videos/download",
             state: "/api/videos/download-state", stop: "/api/videos/download/stop",
             pause: "/api/videos/download/pause", unqueue: "/api/videos/download/unqueue", prefix: /^Скачать с сайта:\s*/ },
    manhwa: { label: "Манга и манхва", find: "/api/manhwa/find", findState: "/api/manhwa/find-state",
              get: "/api/manhwa/download", state: "/api/manhwa/download-state", stop: "/api/manhwa/download/stop",
              pause: "/api/manhwa/download/pause", unqueue: "/api/manhwa/download/unqueue", prefix: /^Скачать:\s*/ },
    manga: { label: "Хентай", get: "/api/manga/download", state: "/api/manga/download-state",
             stop: "/api/manga/download/stop", unqueue: "/api/manga/download/unqueue",      // (v5.44.107) a queue
             prefix: /^(?:С прошлого запуска — )?Скачать с nhentai:\s*/ },
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

  // Remanga's counts of a translators' votes run to millions: «1,1 млн оценок», not «1119791 оценок»
  function votesText(n) {
    if (n >= 1e6) return String(Math.round(n / 1e5) / 10).replace(".", ",") + " млн оценок";
    if (n >= 1e4) return Math.round(n / 1e3) + " тыс. оценок";
    return plural(n, ["оценка", "оценки", "оценок"]);
  }

  function megabytes(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1).replace(".", ",") + " ГБ";
    return Math.max(1, Math.round(n / 1024 / 1024)) + " МБ";
  }

  function speedOf(bytes) {
    const n = Number(bytes) || 0;
    return n < 1024 * 1024 ? Math.max(1, Math.round(n / 1024)) + " КБ/с" : (n / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ/с";
  }

  function clockOf(seconds) {
    const m = Math.max(1, Math.round((Number(seconds) || 0) / 60));
    return m < 60 ? m + " мин" : Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "");
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
      extra = el("span", "gl-step-more", "Мангу и манхву программа берёт с Remanga, ReComics, MangaLib, MangaBuff, "
        + "ReadManga, Desu, com-x.life, MangaDex, хентай — с nhentai, Хентай-тян, HentaiLib и AllHentai; с других "
        + "сайтов — только видео. "
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
  let shown = null;              // {kind, id, count?, tiles?} — what the card shows
  const hitFacts = new Map();    // a title's address → its tile of the search by name (its rating: the card says it)

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
    findLink(w.kind === "site" ? "manhwa" : "video", w.url, "", w.site, fresh);
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
      st = await stateOf("find-" + mine.kind, KINDS[mine.kind].findState);
    } catch (_) {
      if (finding === mine) setTimeout(waitFound, 1500);
      return;
    }
    if (finding !== mine) return;
    if (st.id !== mine.id || RUNNING.includes(st.status)) {
      if (st.id === mine.id && st.line) tell(st.line);
      setTimeout(waitFound, 600);
      return;
    }
    finding = null;
    busy(false);
    if (st.found) {
      tell("");
      showFound(mine.kind, st);
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
    nhGen += 1;
    card.classList.remove("gl-busy");
    card.textContent = "";
    card.dataset.kind = kind;
    shown = { kind, id: st.id, text: (finding && finding.text) || field.value.trim() };
    if (kind === "video") showVideo(st); else showSite(st);
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

  // the segmented choice («Куда», the chapters' language)
  function segmented(name, options, chosen, onPick) {
    const group = el("div", "gl-seg");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", name);
    options.forEach(([value, text, disabled, tip]) => {
      const b = button("", text);
      b.dataset.value = value;
      b.setAttribute("aria-pressed", value === chosen ? "true" : "false");
      b.disabled = !!disabled;
      if (tip) b.title = tip;
      b.addEventListener("click", () => {
        if (b.getAttribute("aria-pressed") === "true") return;
        group.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
        onPick(value);
      });
      group.append(b);
    });
    group.value = () => (group.querySelector('button[aria-pressed="true"]') || {}).dataset.value || chosen;
    return group;
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

  // ── the titles of the sites of manga ─────────────────────────────────────────────────────────────────────────
  function showSite(st) {
    const f = st.found;
    const findId = st.id;
    const hit = hitFacts.get(f.url);
    showMark(f.adult_site ? "Хентай" : f.type || "Манга и манхва");
    const view = hero(f.cover ? "/api/manhwa/find-cover?find=" + encodeURIComponent(findId) : "", f.title,
      [f.type, f.year, f.status, f.age], (f.alt || []).join(" · "));
    if (f.cover_hidden) view.pic.title = "Обложка скрыта (SFW)";
    card.append(view.top);
    const a = view.about;

    // the chapters' language (MangaDex) or the translators, the rating, the counts
    const row = el("div", "gl-row");
    const branches = f.branches || [];
    const again = (branch) => {
      tell(f.languages ? "Читаю главы на этом языке…" : "Читаю главы этих переводчиков…");
      findLink("manhwa", f.url, branch, f.site_label);
    };
    if (f.languages && branches.length > 1) {
      row.append(segmented("Язык глав", branches.map((b) => [b.id, (b.name || b.id) + " · " + b.count]), f.branch, again));
    } else if (branches.length > 1) {
      const pick = el("label", "gl-select");
      const select = el("select");
      branches.forEach((b) => {
        const option = el("option", "", (b.name || "без названия") + " — " + plural(b.count, CHAPTERS)
          + (b.votes ? ", " + votesText(b.votes) : ""));
        option.value = b.id;
        option.selected = b.id === f.branch;
        select.append(option);
      });
      select.addEventListener("change", () => again(select.value));
      pick.append(el("span", "", "Переводчики"), select);
      row.append(pick);
    }
    if (hit && hit.rating) {
      const rate = el("span", "gl-rating");
      rate.append(el("b", "", "★ " + String(hit.rating).replace(".", ",")));
      rate.title = "Оценка на сайте";      // no count: Remanga's «total_votes» of a title are millions — not its ratings
      row.append(rate);
    }
    row.append(el("span", "gl-fact", plural(f.chapters, CHAPTERS)));
    if (f.updated) row.append(el("span", "gl-fact", "последняя — " + f.updated));
    row.append(el("span", "gl-fact", "с " + f.site_label));
    if (branches.length === 1 && branches[0].name && !f.languages) row.append(el("span", "gl-fact", "перевод: " + branches[0].name));
    a.append(row);
    if ((f.genres || []).length) a.append(chips(f.genres, "gl-genres"));
    if (f.about) a.append(about(f.about));
    if (f.about && f.about_site && f.about_site !== f.site_label) {      // (v5.44.112) Remanga's description first
      a.append(el("p", "gl-note", `Описание — с ${f.about_site}, главы — с ${f.site_label}`));
    }
    (f.notes || []).forEach((note) => a.append(el("p", "gl-note warn", note)));

    // what is read and what is downloaded
    const actions = el("div", "gl-actions");
    const readRow = el("span", "gl-read");
    const all = iconButton("gl-btn", ICON.down);
    const getAll = all.node;
    const getMarked = button("gl-btn quiet", "");
    actions.append(readRow, getAll, getMarked);
    a.append(actions);
    const sum = el("p", "gl-sum");
    a.append(sum);

    // how: where, the new chapters by night, the other sites
    const opts = el("div", "gl-opts");
    const rootsOk = !!(f.roots && f.roots.manga);
    const where = segmented("Куда", [["manhwa", "В манхву — лентой"], ["manga", "В мангу — по страницам", !rootsOk,
      rootsOk ? "" : "Папка манги не выбрана — «Настройки → Папки»"]], f.kind === "manga" && rootsOk ? "manga" : "manhwa",
      () => update());
    const whereBox = el("div", "gl-opt");
    whereBox.append(el("span", "gl-opt-label", "Куда"), where);
    const follow = check("Новые главы — скачивать самой (раз в сутки, ночью)", true);
    opts.append(whereBox, follow.node);
    const giving = (f.helpers || []).filter((h) => h.gives > 0);
    let helpers = null;
    if (giving.length) {
      helpers = check("Главы, которых нет бесплатно на " + f.site_label + ", — брать с других сайтов: "
        + giving.map((h) => h.site_label + " (" + plural(h.gives, CHAPTERS) + ")").join(", "), f.helpers_on !== false);
      opts.append(helpers.node);
    } else if ((f.helpers || []).length) {
      opts.append(el("p", "gl-note", "Тайтл есть и на " + f.helpers.map((h) => h.site_label).join(", ")
        + " — новых глав оттуда сейчас нет."));
    }
    const paid = el("p", "gl-note");
    opts.append(paid);
    card.append(opts);
    const others = () => (helpers ? helpers.tick.checked : f.helpers_on !== false);

    // the chapters: tiles, by tomes; ticked by hand
    const pick = chaptersGrid(f, others, update);
    card.append(pick.node);
    if (helpers) helpers.tick.addEventListener("change", () => { pick.repaint(); update(); });

    // the download going: of another title — «В очередь», of this one — its tiles fill, nothing to ask again
    function going() {
      const job = jobs.manhwa;
      if (!job || !RUNNING.includes(job.status)) return "";
      return String(job.find) === String(findId) ? "this" : "other";
    }

    function update() {
      const open = pick.open();
      const chosen = pick.chosen().length;
      const waiting = pick.waiting();
      const now = going();
      const queued = now === "other";
      // nothing to take now, nothing in the folder yet: the title kept, its paid ones awaited by the nightly round
      const waits = !open && waiting && !f.have;
      all.words.textContent = now === "this" ? "Качается — в «Загрузках»" : open ? (queued ? "В очередь — " : "Скачать всё — ")
        + plural(open, CHAPTERS) : waits ? "Ждать платные" : "Всё уже в папке";
      getAll.disabled = now === "this" || (!open && !waits);
      getMarked.textContent = (queued ? "В очередь отмеченные" : "Скачать отмеченные") + " (" + chosen + ")";
      getMarked.disabled = !chosen || now === "this";
      pick.go.textContent = getMarked.textContent;
      pick.go.hidden = !chosen || now === "this";
      const folder = folderName(where.value() === f.kind ? f.folder : f.title);
      sum.textContent = "В папку " + (where.value() === "manga" ? "манги" : "манхвы") + " — «" + folder + "»"
        + (f.have ? ", там уже " + plural(f.have, CHAPTERS) : "") + (queued ? " · начнётся после текущего скачивания" : "");
      const on = others();
      const left = on ? f.paid_left : f.paid_count;
      const covered = on ? f.paid_count - f.paid_left : 0;
      const soon = (items) => (items || []).slice(0, 3).map((c) => c.label + (c.free_at ? " — " + c.free_at : "")).join("; ");
      const dated = (items) => (items || []).some((c) => c.free_at);
      const nowhere = "бесплатно их пока нет ни на одном из сайтов — появятся, программа скачает сама.";
      let words = "";
      if (f.paid_count && covered && !left) {
        words = "Платных на " + f.site_label + ": " + f.paid_count + " — все есть бесплатно на других сайтах.";
      } else if (f.paid_count && covered) {
        words = "Платных: " + f.paid_count + ", из них " + covered + " есть бесплатно на других сайтах; " + (dated(f.waiting)
          ? "остальные программа возьмёт, когда сайт сделает их бесплатными" + (soon(f.waiting) ? ": " + soon(f.waiting) : "")
          + "." : "остальные " + left + " — " + nowhere);
      } else if (f.paid_count) {
        words = "Платных: " + f.paid_count + " — " + (dated(f.paid) ? "их программа не покупает и возьмёт сама, когда сайт "
          + "сделает их бесплатными" + (soon(f.paid) ? ": " + soon(f.paid) : "") + "." : nowhere);
      }
      paid.textContent = words;
      paid.hidden = !words;
    }

    async function download(chapters) {
      getAll.disabled = true;
      getMarked.disabled = true;
      try {
        const reply = await postJson(KINDS.manhwa.get, { find: findId, where: where.value(), follow: follow.tick.checked,
          helpers: others(), chapters });
        LB.say(null, reply.queued ? (reply.queued > 1 ? "В очереди " + reply.queued + "-м" : "В очереди следующим")
          + ": начнётся само" : "Скачивание началось — оно справа, в «Загрузках»");
        if (chapters) pick.clear();
        watch("manhwa", true);
      } catch (err) {
        LB.say(null, err.message, true);
      }
      update();
    }
    getAll.addEventListener("click", () => download(null));
    getMarked.addEventListener("click", () => download(pick.chosen()));
    pick.go.addEventListener("click", () => download(pick.chosen()));
    showRead(f, readRow);
    update();
    shown.count = update;
    shown.tiles = pick;
    shown.url = f.url;
  }

  // (v5.44.82) «Читать с начала», «Продолжить: Глава 12»: the title read without downloading it (/online)
  function onlineHref(f, n, extra) {
    return "/online?url=" + encodeURIComponent(f.url) + (f.branch ? "&branch=" + encodeURIComponent(f.branch) : "")
      + "&ch=" + n + (extra || "");
  }

  function showRead(f, row) {
    const list = f.list || [];
    const first = list.findIndex((c) => !c.wait);
    if (first < 0) return;
    const start = el("a", "gl-btn primary", "Читать с начала");
    start.prepend(svg(ICON.play, 16));
    start.href = onlineHref(f, first + 1, "&from=start");
    row.append(start);
    getJson("/api/online/last?url=" + encodeURIComponent(f.url)).then((reply) => {
      const last = reply && reply.last;
      if (!last || !last.ch || !shown || shown.url !== f.url) return;
      const n = last.done && last.ch < list.length ? last.ch + 1 : last.ch;
      const go = el("a", "gl-btn primary", "Продолжить: " + ((list[n - 1] && list[n - 1].label) || last.label));
      go.prepend(svg(ICON.play, 16));
      go.href = onlineHref(f, n);
      start.className = "gl-btn";
      start.firstChild.remove();
      row.prepend(go);
    }).catch(() => { /* nothing kept: «Читать с начала» only */ });
  }

  // the chapters as tiles of one size, a tome at a time: in the folder «есть», the paid ones nobody has free locked,
  // another site's — from there (or not, without that box); a tile ticked by a click, «▶» — read it
  function chaptersGrid(f, others, changed) {
    const node = el("section", "gl-pick");
    node.setAttribute("aria-label", "Главы");
    const list = f.list || [];
    const tomes = new Map();
    list.forEach((c, i) => {
      const key = Number(c.tome) || 0;
      if (!tomes.has(key)) tomes.set(key, []);
      tomes.get(key).push([c, i + 1]);
    });
    const go = button("gl-btn small", "");
    go.hidden = true;
    const head = gridHead("Главы", list.length, [null, quick([["all", "все"], ["none", "ничего"]], (how) => {
      boxes().forEach((t) => { if (!t.disabled) t.checked = how === "all"; paintTile(t); });
      changed();
    }), go]);
    if (tomes.size > 1) {
      const tabs = el("div", "gl-tabs");
      tabs.setAttribute("role", "tablist");
      tabs.setAttribute("aria-label", "Тома");
      const keys = ["all", ...tomes.keys()];
      keys.forEach((key) => {
        const b = button("", key === "all" ? "Все" : key ? "Том " + key : "Без тома");
        b.setAttribute("role", "tab");
        b.dataset.tome = String(key);
        b.setAttribute("aria-selected", key === "all" ? "true" : "false");
        b.addEventListener("click", () => {
          tabs.querySelectorAll("button").forEach((x) => x.setAttribute("aria-selected", x === b ? "true" : "false"));
          grid.querySelectorAll(".gl-cell").forEach((cell) => {
            cell.hidden = key !== "all" && cell.dataset.tome !== String(key);
          });
        });
        tabs.append(b);
      });
      head.insertBefore(tabs, head.children[1]);
    }
    head.append(legend([["have", "в папке"], ["on", "отмечена"], ["from", "с другого сайта"], ["wait", "платная везде"]]));
    node.append(head);
    const grid = el("div", "gl-grid");
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-label", "Главы");
    list.forEach((c, i) => grid.append(tile(c, i + 1)));
    node.append(grid);
    if (!list.length) node.append(el("p", "gl-note", "Глав у тайтла нет ни на одном из сайтов."));
    grid.addEventListener("change", (event) => {
      if (event.target.matches("input[type='checkbox']")) { paintTile(event.target); changed(); }
    });

    function base(c) {
      if (c.have) return { ok: false, mark: "есть", kind: "have" };
      if (c.wait) return { ok: false, mark: "платная", kind: "wait" };
      if (c.from) return others() ? { ok: true, mark: "с " + c.from, kind: "from" } : { ok: false, mark: c.paid ? "платная" : "нет", kind: "wait" };
      return { ok: true, mark: "", kind: "" };
    }

    function tile(c, n) {
      const cell = el("div", "gl-cell");
      cell.dataset.tome = String(Number(c.tome) || 0);
      cell.dataset.id = String(c.id);
      const label = el("label", "gl-tile");
      const tick = el("input", "gl-vh");
      tick.type = "checkbox";
      tick.value = String(c.id);
      label.append(tick, el("span", "gl-tile-icon"), el("span", "gl-tile-n", c.n || "•"), el("span", "gl-tile-fill"));
      cell.append(label);
      if (!c.wait) {
        const read = el("a", "gl-tile-read");
        read.append(svg(ICON.play, 12));
        read.href = onlineHref(f, n);
        read.title = "Читать без скачивания";
        read.setAttribute("aria-label", "Читать «" + c.label + "» без скачивания");
        cell.append(read);
      }
      return cell;
    }

    function boxes() { return [...grid.querySelectorAll("input[type='checkbox']")]; }

    function repaint() {
      const byId = new Map(list.map((c) => [String(c.id), c]));
      boxes().forEach((t) => {
        const c = byId.get(t.value);
        const b = base(c);
        t.disabled = !b.ok;
        if (!b.ok) t.checked = false;
        const cell = t.closest(".gl-cell");
        cell.dataset.state = b.kind;
        const icon = cell.querySelector(".gl-tile-icon");
        icon.textContent = "";
        if (b.kind === "have") icon.append(svg(ICON.check, 12));
        else if (b.kind === "wait") icon.append(svg(ICON.lock, 12));
        cell.title = [c.label + (c.name ? " — " + c.name : ""), b.kind === "have" ? "Уже в папке" : "",
          c.free_at ? "Платная — бесплатной станет " + c.free_at : b.kind === "wait" ? "Платная — бесплатно её нет нигде" : "",
          b.kind === "from" ? "Скачается с " + c.from : ""].filter(Boolean).join("\n");
        t.setAttribute("aria-label", c.label + (c.name ? " — " + c.name : "") + (b.mark ? ", " + b.mark : ""));
        paintTile(t);
      });
    }
    repaint();

    // (v5.44.74) the download going of this title: each tile fills as its strips come
    function paintRun(items) {
      items.forEach((x) => {
        const cell = grid.querySelector('.gl-cell[data-id="' + CSS.escape(String(x.id)) + '"]');
        if (!cell) return;
        cell.dataset.dl = x.state;
        const p = x.state === "done" ? 100 : x.pages ? Math.round(100 * (x.got || 0) / x.pages) : 0;
        cell.style.setProperty("--p", p + "%");
        const said = { wait: "ждёт скачивания", going: "качается", paused: "пауза", retry: "повтор", done: "скачана",
          failed: "не скачалась" + (x.why ? ": " + x.why : "") }[x.state] || "";
        cell.title = (x.label || "") + (said ? " — " + said : "");
      });
    }

    return {
      node, go, repaint, paintRun,
      chosen: () => boxes().filter((t) => t.checked && !t.disabled).map((t) => t.value),
      open: () => boxes().filter((t) => !t.disabled).length,
      waiting: () => list.filter((c) => base(c).kind === "wait").length,
      clear: () => { boxes().forEach((t) => { t.checked = false; paintTile(t); }); },
    };
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
    if ((facts.genres || []).length) a.append(chips(facts.genres, "gl-genres"));
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
    const voices = f.voices || [];
    if (f.choose === "dubs" && voices.length) {
      a.append(el("p", "gl-note", "Только в плеерах, из которых программа не скачивает: " + voices.map((v) => v.name
        + (v.players && v.players.length ? " (" + v.players.join(", ") + ")" : "")).join(", ") + "."));
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
    // program says which links it shows: /api/watch/can)
    fetch("/api/watch/can?url=" + encodeURIComponent(f.page_url || ""), { credentials: "same-origin" })
      .then((r) => r.json()).then((can) => {
        if (!can || !can.watch || actions.querySelector(".gl-watch")) return;
        const watch = el("a", "gl-btn gl-watch", "Смотреть");
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
      go.disabled = !files.length;
      goButton.words.textContent = files.length ? (other ? "В очередь: " : "Скачать ") + said : pick.empty();
      pick.go.textContent = goButton.words.textContent;
      pick.go.hidden = !files.length;
      let bytes = pick.weight(files);
      let approx = bytes > 0;
      if (!bytes) {
        files.forEach((i) => { bytes += Number(f.files[i].size) || 0; approx = approx || !!f.files[i].stream; });
      }
      sum.textContent = (files.length && bytes ? (approx ? "≈ " : "") + megabytes(bytes) + ", " : "")
        + "в папку «" + folderName(f.folder) + "» — " + (f.new_folder ? "новая" : "уже есть, новые серии лягут в неё")
        + (other ? " · начнётся после текущего скачивания" : "");
    }

    async function download() {
      const files = pick.chosen();
      if (!files.length) return;
      go.disabled = true;
      try {
        const reply = await postJson(KINDS.video.get, { find: findId, files, quality: pick.quality(), merge: pick.merge() });
        LB.say(null, reply.queued ? (reply.queued > 1 ? "В очереди " + reply.queued + "-м" : "В очереди следующим")
          + ": начнётся само" : "Скачивание началось — оно справа, в «Загрузках»");
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
    merge.tick.addEventListener("change", changed);
    row.append(qualityBox, merge.node);
    node.append(row);
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
      } else if (t.closest(".gl-grid")) {
        if (t.checked) episodes.add(t.value); else episodes.delete(t.value);
      } else {
        return;
      }
      paint();
    });

    function facts(list) {
      let best = 0;
      const players = [];
      list.forEach((x) => {
        best = Math.max(best, parseInt(String(x.quality || "").replace(/\D+/g, ""), 10) || 0);
        String(x.note || "").replace("Плеер: ", "").split(", ").forEach((p) => { if (p && !players.includes(p)) players.push(p); });
      });
      return [best ? "до " + best + "p" : "", players.join(", ")].filter(Boolean).join(" · ");
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
        text.append(el("b", "", shortName(name)), el("span", "gl-muted", [facts(list),
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
      changed();
    }

    function picked() {
      return f.files.filter((x) => episodes.has(x.label || "") && chosen.has(x.group || "") && x.have !== "whole"
        && x.have !== "other" && !x.blocked).map((x) => x.i);
    }

    // what the chosen ones take on the disk: one episode measured in each quality (/api/videos/measure), as the
    // computer's page of it counts (videos.js estimate)
    function weight(files) {
      const m = measured;
      if (!m || !m.sizes || !m.seconds || !files.length) return 0;
      const heights = Object.keys(m.sizes).map(Number).sort((a, b) => b - a);
      const cap = quality.value === "best" ? 1e9 : Number(quality.value) || 1e9;
      const sizeAt = (limit) => {
        const h = heights.find((x) => x <= Math.min(cap, limit)) || heights[heights.length - 1];
        return m.sizes[String(h)] || 0;
      };
      const limitOf = (x) => (/CVH/.test(x.note || "") ? 1e9 : /AniLiberty/.test(x.note || "") ? 1080 : 720);
      const sound = 14000 * m.seconds;
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
        total += (had ? 0 : sizeAt(Math.max(...voices.map(limitOf)))) + (voices.length - (had ? 0 : 1)) * sound;
      });
      return total;
    }
    getJson("/api/videos/measure?find=" + encodeURIComponent(findId)).then((reply) => {
      measured = reply.measured || {};
      changed();
    }).catch(() => { measured = {}; });
    setTimeout(paint, 0);

    return {
      node, go, weight,
      chosen: picked,
      empty: () => (chosen.size ? "Отметьте серии" : "Выберите озвучку"),
      quality: () => quality.value,
      merge: () => merge.tick.checked,
    };
  }

  // ── nhentai ───────────────────────────────────────────────────────────────────────────────────────────────────
  // (v5.44.97) one gallery — its card: the cover, the titles, the tags, the thumbnails of its pages (asked of the
  // program: /api/manga/gallery — the API of the site; a tile of «Манга → Лента» leads here); «Скачать» stays when the
  // description does not come; a gallery the program's rule leaves out — said, nothing of it shown
  function showNhentai(ids) {
    const gen = ++nhGen;
    card.textContent = "";
    card.dataset.kind = "manga";
    shown = { kind: "manga", id: 0 };
    const one = ids.length === 1;
    const view = hero("", one ? "Галерея nhentai " + ids[0] : "Галереи nhentai: " + ids.length, ["Хентай", "nhentai"], "");
    card.append(view.top);
    if (!one) {
      view.about.append(chips(ids.slice(0, 40).map((n) => "№ " + n), "gl-numbers"));
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
            + " — справа, в «Загрузках»" : "Скачивание началось — оно справа, в «Загрузках»");
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
      if (g && g.forbidden) {                    // the program's rule: no picture, no name, no «Скачать»
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
      line.append(chips(group.names.slice(0, 40), "gl-genres"));
      a.append(line);
    });
    if (g.have) {
      const there = el("p", "gl-note", "");
      there.append("Уже в папке манги: ", el("b", "", g.have.folder),
        g.have.pages >= g.pages ? " — все страницы на месте." : " — страниц там " + g.have.pages + " из " + g.pages
          + ", «Скачать» докачает остальные.");
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
    hitFacts.set(t.url, t);
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
  async function restoreHits(text, url) {
    if (/^https?:\/\//i.test(text)) return;
    try {
      const st = await getJson("/api/manhwa/search-state?id=last");
      const same = (x) => String(x || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (!st.id || hitsId || same(st.query) !== same(text)) return;
      startHits(st.id, text);
      showHits(st);
      markPicked(url || "");
      if (url) { hitsBox.classList.add("folded"); showFold(); }
      if (!st.done) pollHits();
    } catch (_) { /* the search is gone: the card alone */ }
  }

  function pickHit(url) {
    const tile = hitTiles.get(url);
    if (!tile || !tile.hit) return;
    markPicked(url);
    hitsBox.classList.add("folded");
    showFold();
    hideCard();
    findLink("manhwa", url, "", ((tile.hit.sites || [])[0] || {}).label || "");
    hitsBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  hitsFold.querySelector("button").addEventListener("click", () => {
    hitsBox.classList.remove("folded");
    hitsFold.hidden = true;
  });

  // ── «Загрузки»: the downloads going of the three, their queues ──────────────────────────────────────────────
  const jobs = {};               // kind -> its last state
  const items = {};              // kind -> its item in the list
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
    paintJob(kind, st);
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
      const st = await getJson(KINDS[kind].findState + "?after=999999999");
      if (shown === mine && st.found && st.id === mine.id) showFound(kind, st);
    } catch (_) { /* the card stays as it was */ }
  }

  function item(kind) {
    if (items[kind]) return items[kind];
    const li = el("li", "gl-job");
    li.dataset.kind = kind;
    const pic = el("span", "gl-job-pic");
    const initial = el("span", "gl-initial", "");
    initial.setAttribute("aria-hidden", "true");
    pic.append(initial);
    const body = el("div", "gl-job-body");
    const top = el("div", "gl-job-top");
    const title = el("b", "gl-job-title");
    const kindWord = el("span", "gl-job-kind", KINDS[kind].label);
    top.append(title, kindWord);
    const text = el("p", "gl-job-text");
    const bar = el("span", "gl-job-bar");
    const fill = el("i");
    bar.append(fill);
    const sub = el("p", "gl-job-sub");
    const queue = el("ul", "gl-job-queue");
    const tools = el("div", "gl-job-tools");
    const pause = button("gl-icon-btn", "");
    const stop = button("gl-icon-btn", "");
    stop.append(svg(ICON.stop, 14));
    stop.title = "Остановить";
    stop.setAttribute("aria-label", "Остановить");
    const open = el("a", "gl-link", "Открыть");
    tools.append(pause, stop, open);
    body.append(top, text, bar, sub, queue, tools);
    li.append(pic, body);
    stop.addEventListener("click", async () => {
      stop.disabled = true;
      try { await postJson(KINDS[kind].stop, {}); LB.say(null, "Останавливаю…"); } catch (err) { LB.say(null, err.message, true); }
      stop.disabled = false;
      watch(kind, true);
    });
    pause.addEventListener("click", async () => {
      const st = jobs[kind] || {};
      pause.disabled = true;
      try {
        const reply = await postJson(KINDS[kind].pause, { pause: !st.paused });
        LB.say(null, reply.paused ? (kind === "manhwa" ? "Пауза: глава остановится, где она есть" : "Пауза: файл остановится, где он есть") : "Продолжаю");
      } catch (err) {
        LB.say(null, err.message, true);
      }
      pause.disabled = false;
      watch(kind, true);
    });
    items[kind] = { li, pic, initial, title, kindWord, text, bar, fill, sub, queue, pause, stop, open, picture: "" };
    jobsList.append(li);
    return items[kind];
  }

  // the item's picture; one that did not come (a cover not written yet) is asked again, not more than every 10 s
  function picture(it, address, letter) {
    it.initial.textContent = String(letter || "").trim().charAt(0).toUpperCase();
    if ((address || "") === it.picture) return;
    if (address && it.failed && it.failed.address === address && Date.now() - it.failed.at < 10000) return;
    it.picture = address || "";
    it.pic.querySelectorAll("img").forEach((x) => x.remove());
    it.pic.classList.remove("has-picture");
    if (!address) return;
    const img = el("img");
    img.alt = "";
    img.decoding = "async";
    img.onload = () => it.pic.classList.add("has-picture");
    img.onerror = () => {
      img.remove();
      it.failed = { address, at: Date.now() };
      if (it.picture === address) it.picture = "";
    };
    img.src = address;
    it.pic.append(img);
  }

  function paintJob(kind, st) {
    const soon = !!KINDS[kind].unqueue && st.restoring;
    const queue = KINDS[kind].unqueue ? st.queue || [] : [];
    if (!st.id && !soon && !queue.length) {
      if (items[kind]) items[kind].li.hidden = true;
      sideSummary();
      return;
    }
    const it = item(kind);
    it.li.hidden = false;
    const running = RUNNING.includes(st.status);
    it.li.dataset.state = running ? (st.paused ? "paused" : "running") : soon && !st.id ? "soon" : st.status || "idle";
    let title = String(st.label || "").replace(KINDS[kind].prefix, "").replace(/^«(.*)»$/, "$1") || (soon ? "С прошлого запуска" : "");
    it.stop.hidden = !running && !queue.length;
    it.stop.title = queue.length ? "Остановить и убрать очередь" : "Остановить";
    it.stop.setAttribute("aria-label", it.stop.title);
    it.pause.hidden = !KINDS[kind].pause || !running || st.status === "stopping";
    it.pause.textContent = "";
    it.pause.append(svg(st.paused ? ICON.play : ICON.pause, 14));
    it.pause.title = st.paused ? "Продолжить" : "Пауза";
    it.pause.setAttribute("aria-label", it.pause.title);
    it.queue.textContent = "";
    const said = { text: "", sub: "", part: running ? 0.02 : 1, open: "", pic: "", kind: KINDS[kind].label };
    if (kind === "video") paintVideo(st, said, running, soon, queue, it);
    else if (kind === "manhwa") paintManhwa(st, said, running, soon, queue, it);
    else paintManga(st, said, running, soon, queue, it);
    if (!running && st.status === "failed") said.text = st.error || "Не вышло — причина в журнале";
    title = said.title || title;
    it.title.textContent = title;
    it.title.title = title;
    it.kindWord.textContent = said.kind;
    it.text.textContent = said.text;
    it.text.hidden = !said.text;
    it.sub.textContent = said.sub;
    it.sub.hidden = !said.sub;
    it.bar.hidden = !running;
    it.fill.style.width = Math.max(2, Math.min(100, said.part * 100)).toFixed(1) + "%";
    it.open.hidden = running || !said.open;
    it.open.href = said.open || "#";
    it.queue.hidden = !it.queue.children.length;
    picture(it, said.pic, title || said.kind);
    // the downloads going first, then the ones that have ended
    const order = (x) => (x.li.dataset.state === "running" || x.li.dataset.state === "paused" ? 0 : x.li.dataset.state === "soon" ? 1 : 2);
    const wanted = ORDER.map((k) => items[k]).filter(Boolean).sort((a, b) => order(a) - order(b)).map((x) => x.li);
    const now = [...jobsList.children];
    if (now.length !== wanted.length || now.some((li, k) => li !== wanted[k])) jobsList.append(...wanted);
    sideSummary();
  }

  function queueLines(kind, queue, it, what) {
    queue.forEach((q, n) => {
      const li = el("li");
      li.append(el("span", "gl-job-q", (n ? "Потом" : "Дальше") + ": «" + q.title + "»" + (what(q) ? " — " + what(q) : "")));
      li.append(button("gl-link", "убрать", async () => {
        try { await postJson(KINDS[kind].unqueue, { id: q.id }); } catch (err) { LB.say(null, err.message, true); }
        watch(kind, true);
      }));
      it.queue.append(li);
    });
  }

  function restoreWords(soon) {
    return "С прошлого запуска программы продолжится: " + soon.titles.map((t) => "«" + t + "»").join(", ")
      + (soon.in > 0 ? " — через " + soon.in + " с" : " — сейчас");
  }

  function paintVideo(st, said, running, soon, queue, it) {
    const m = st.meter || {};
    if (running && m.count) {
      if (m.merging) {
        said.text = "Собираю озвучки в общие файлы серий: " + (m.name || "");
        said.part = Math.max(0, (Number(m.index) || 1) - 1) / m.count;
      } else {
        const part = m.total ? Math.min(1, m.done / m.total) : 0;
        said.part = Math.min(1, ((Number(m.index) || 1) - 1 + part) / m.count);
        const name = String(m.name || "Файл").replace(/\.[^.]+$/, "");
        const bits = [];
        if (m.total) bits.push(Math.floor(part * 100) + "% из " + megabytes(m.total));
        else if (m.done) bits.push(megabytes(m.done));
        if (m.speed && !st.paused) bits.push(megabytes(m.speed) + "/с");
        said.text = (st.paused ? "Пауза — " : "") + name + (bits.length ? " · " + bits.join(" · ") : "");
      }
      said.sub = "Файлов: " + m.index + " из " + m.count;
    } else if (running) {
      said.text = st.line || "Начинаю…";
    } else if (st.id) {
      const sum = st.summary || {};
      const done = Number(sum.done) || 0, failed = Number(sum.failed_files) || 0, had = Number(sum.whole) || 0;
      said.text = (st.status === "stopped" || sum.stopped ? "Остановлено: " : "Готово: ")
        + (done ? "скачано " + plural(done, ["файл", "файла", "файлов"]) : "ничего не скачано")
        + (had ? ", уже были: " + had : "") + (failed ? ", не скачались: " + failed : "")
        + (Number(sum.merged) ? ", собрано в общие файлы: " + plural(Number(sum.merged), ["серия", "серии", "серий"]) : "");
      said.open = st.series_url || "";
    }
    if (soon) said.sub = restoreWords(soon);
    if (shown && shown.kind === "video" && shown.poster && String(st.find) === String(shown.id)) {
      said.pic = "/api/videos/find-poster?find=" + encodeURIComponent(shown.id);
    }
    queueLines("video", queue, it, (q) => plural(q.files, ["файл", "файла", "файлов"]));
  }

  function paintManhwa(st, said, running, soon, queue, it) {
    const m = st.meter || {};
    const now = m.now;
    said.kind = st.what || KINDS.manhwa.label;
    if (running && m.total) {
      const part = now && now.pages ? Math.min(1, now.got / now.pages) : 0;
      said.part = Math.min(1, ((m.done || 0) + (m.failed || 0) + part) / m.total);
      const bits = [];
      if (now) bits.push(now.label + (now.from ? " (с " + now.from + ")" : "") + (now.pages ? ": " + now.got + " из " + now.pages : ""));
      if (m.speed && !st.paused) bits.push(speedOf(m.speed));
      if (m.eta && !st.paused) bits.push("осталось ≈ " + clockOf(m.eta));
      said.text = (st.paused ? "Пауза" + (bits.length ? " — " : "") : "") + (bits.join(" · ") || (st.paused ? "" : st.line || "Начинаю…"));
      said.sub = "Глав: " + (m.done || 0) + " из " + m.total + (m.retrying ? ", ждут повтора: " + m.retrying : "")
        + (m.failed ? ", не скачались: " + m.failed : "");
    } else if (running) {
      said.text = st.line || "Начинаю…";
    }
    const result = st.result;
    if (!running && result) {
      const failed = (result.failed || []).length;
      said.text = (result.got ? "Скачано: " + plural(result.got, CHAPTERS) : "Новых глав не скачано")
        + (failed ? ", не скачались: " + failed : "") + (result.stopped ? " (остановлено)" : "")
        + (result.gave_up ? ". " + result.gave_up : "");
      said.open = result.open || "";
      (result.failed || []).slice(-3).forEach((x) => {
        it.queue.append(el("li", "warn", x.label + ": не скачалась" + (x.why ? " — " + x.why : "")));
      });
    }
    if (soon) said.sub = restoreWords(soon);
    // the cover is in its folder once the first chapter goes (the download writes the title first)
    if (m.run && ((running && (m.now || m.done)) || result)) said.pic = "/api/manhwa/download-cover?run=" + m.run;
    queueLines("manhwa", queue, it, (q) => (q.count ? plural(q.count, CHAPTERS) : ""));
  }

  const MANGA_STATES = { wait: "в очереди", reading: "читаю описание…", done: "готово", had: "уже была скачана",
    failed: "не скачана", stopped: "остановлена — докачается" };

  function paintManga(st, said, running, soon, queue, it) {
    const list = st.items || [];
    const done = list.filter((x) => x.state === "done" || x.state === "had").length;
    const now = list.find((x) => x.state === "pages" || x.state === "reading");
    if (running) {
      said.part = list.length ? (done + (now && now.pages ? (now.page || 0) / now.pages : 0)) / list.length : 0.02;
      said.text = now ? (now.title || "Галерея " + now.number) + (now.state === "pages" ? ": страница " + (now.page || 1)
        + " из " + (now.pages || "?") : ": читаю описание…") : st.line || "Начинаю…";
      said.sub = list.length > 1 ? "Галерей: " + done + " из " + list.length : "";
    } else if (st.id) {
      const failed = list.filter((x) => x.state === "failed").length;
      said.text = (st.status === "stopped" ? "Остановлено: " : "Готово: ") + plural(done, ["галерея", "галереи", "галерей"])
        + (failed ? ", не скачаны: " + failed : "");
      said.open = st.open || "";
    }
    // a gallery's cover is its first page: there once its second is coming
    const covered = (x) => x.cover && (x.state === "done" || x.state === "had" || (x.state === "pages" && (x.page || 0) > 1));
    const withCover = now && covered(now) ? now : list.find(covered);
    if (withCover) said.pic = withCover.cover;
    if (list.length === 1 && list[0].title) said.title = list[0].title;      // one gallery: its own name
    // (v5.44.107) the galleries waiting — the queue, each with «убрать»; the others — their lines
    const waits = new Set(running ? queue.map((q) => String(q.id)) : []);
    const lines = list.filter((x) => !(x.state === "wait" && waits.has(String(x.number))));
    if (list.length > 1) {
      lines.slice(-8).forEach((x) => {
        const state = x.state === "pages" ? "страница " + (x.page || 1) + " из " + (x.pages || "?") : MANGA_STATES[x.state] || "";
        it.queue.append(el("li", x.state === "failed" ? "warn" : "", (x.title || "Галерея " + x.number) + " — " + state
          + (x.state === "failed" && x.why ? ": " + x.why : "")));
      });
    }
    if (soon) said.sub = restoreWords(soon);
    if (running) queueLines("manga", queue, it, () => "");
  }

  function sideSummary() {
    const shownItems = ORDER.map((k) => items[k]).filter((x) => x && !x.li.hidden);
    const going = shownItems.filter((x) => x.li.dataset.state === "running" || x.li.dataset.state === "paused").length;
    const queued = ORDER.reduce((s, k) => s + (KINDS[k].unqueue && jobs[k] ? (jobs[k].queue || []).length : 0), 0);
    sideEmpty.hidden = shownItems.length > 0;
    sideCount.textContent = [going ? (going === 1 ? "1 идёт" : going + " идут") : "", queued ? queued + " в очереди" : ""]
      .filter(Boolean).join(" · ");
    const pausable = ORDER.filter((k) => KINDS[k].pause && jobs[k] && RUNNING.includes(jobs[k].status) && jobs[k].status !== "stopping");
    sideButtons.hidden = !going && !queued;
    const pauseAll = sideButtons.querySelector('[data-all="pause"]');
    pauseAll.hidden = !pausable.length;
    pauseAll.textContent = pausable.length && pausable.every((k) => jobs[k].paused) ? "Продолжить" : "Пауза";
  }

  sideButtons.addEventListener("click", async (event) => {
    const b = event.target.closest("[data-all]");
    if (!b) return;
    b.disabled = true;
    if (b.dataset.all === "pause") {
      const pausable = ORDER.filter((k) => KINDS[k].pause && jobs[k] && RUNNING.includes(jobs[k].status));
      const pause = !pausable.every((k) => jobs[k].paused);
      for (const k of pausable) {
        try { await postJson(KINDS[k].pause, { pause }); } catch (err) { LB.say(null, err.message, true); }
        watch(k, true);
      }
    } else {
      for (const k of ORDER) {
        const st = jobs[k];
        if (!st || !(RUNNING.includes(st.status) || (st.queue || []).length)) continue;
        try { await postJson(KINDS[k].stop, {}); } catch (err) { LB.say(null, err.message, true); }
        watch(k, true);
      }
      LB.say(null, "Останавливаю…");
    }
    b.disabled = false;
  });

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
    if (last && last.text === text && KINDS[last.kind] && KINDS[last.kind].findState) {
      getJson(KINDS[last.kind].findState + "?after=999999999").then((st) => {
        if (st.id !== last.id) { ask(text); return; }
        if (RUNNING.includes(st.status)) { finding = last; busy(true); waitFound(); }
        else if (st.found && !shown) showFound(last.kind, st);
        else if (!st.found) { ask(text); return; }
        if (last.kind === "manhwa") restoreHits(text, last.url);
      }).catch(() => ask(text));
    } else {
      ask(text);
    }
  }
})();
