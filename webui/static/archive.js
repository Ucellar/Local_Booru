// Local Booru «Скачать → Из архива» (v5.44.76, webui/pages/manga_archive.py, core/manga_archive.py): the titles of the
// manga and manhwa folders the program did not download — «Узнать» (found on the sites by their names, linked when
// sure), the covers to choose from, «Докачать» (the title's card of «Скачать → Манга / Манхва»), «Отвязать»; the work
// is a page task, its journal under «Подробности».  Rows are kept by their key: a refresh while one is typed into does
// not take the text away.  (v5.44.81) How each was known, what was tried for one not found; a round of the background
// leaves the buttons free (they stop it).  (v5.44.116) «Узнавать само, в фоне» and «…и по картинкам» — fields of
// «Настройки → Манга и манхва» now (the page says so in a line).
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const box = document.getElementById("archive");
  if (!box || !LB.watchTask || !LB.postJson) return;
  const actions = LB.actions || {};
  const postJson = LB.postJson;
  const say = LB.say || (() => {});
  const list = box.querySelector(".ar-list");
  const filters = box.querySelector(".ar-filters");
  const empty = box.querySelector(".ar-empty");
  const filesNote = box.querySelector(".ar-files");
  const job = box.querySelector('[data-job="archive"]');
  const step = job.querySelector(".ar-step");
  const stopButton = job.querySelector('[data-action="ar-stop"]');
  const mainButtons = [...box.querySelectorAll(".ar-actions .ar-btn")];
  const HOWS = { link: "по ссылке на сайт в файлах папки", names: "по названию из файлов папки",
    folder: "по имени папки", pictures: "по картинкам (SauceNAO)", query: "по вашему названию", chosen: "выбран вами",
    url: "по вашей ссылке" };

  const FILTERS = [["all", "Все"], ["new", "Не проверены"], ["choose", "Выбрать"], ["none", "Не нашлось"],
    ["linked", "Узнаны"], ["missing", "Не хватает глав"], ["own", "Скачаны программой"]];
  const CHAPTERS = ["глава", "главы", "глав"];
  const PAGES = ["страница", "страницы", "страниц"];
  let entries = [];
  let filter = "all";
  let lastLine = "";
  let refreshTimer = 0;
  const rows = new Map();          // key → its <li>
  const working = new Set();       // keys a task goes for now

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  function word(n, forms) {
    const d = n % 10, h = n % 100;
    return d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2];
  }
  const plural = (n, forms) => n + " " + word(n, forms);
  // (v5.44.87) «Докачать» — the title's card on «Скачать → По ссылке», looked for at once
  const getPage = (e) => "/get?url=" + encodeURIComponent(e.url) + "&go=1";

  function matches(e, key) {
    if (key === "all") return true;
    if (key === "missing") return !!e.counts && e.counts.missing > 0;
    return e.state === key;
  }

  // ── the list ──
  async function refresh(full) {
    try {
      const r = await fetch("/api/archive/state" + (full ? "?refresh=1" : ""), { credentials: "same-origin" });
      const st = await r.json();
      entries = st.entries || [];
      showFilters();
      showRows();
      filesNote.hidden = !st.files;
      filesNote.textContent = st.files ? `Отдельных файлов в корне папки манги: ${st.files}. Программа узнаёт только ` +
        "папки — положите тома одного тайтла в его папку, и он появится здесь." : "";
    } catch (err) {
      say(mainButtons[0], "Список не пришёл: " + err.message, true);
    }
  }

  function refreshSoon(delay) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refresh(false), delay);
  }

  function showFilters() {
    if (filter !== "all" && !entries.some((e) => matches(e, filter))) filter = "all";
    filters.textContent = "";
    FILTERS.forEach(([key, name]) => {
      const n = entries.filter((e) => matches(e, key)).length;
      if (key !== "all" && !n) return;
      const button = el("button", "ar-filter" + (key === filter ? " on" : ""));
      button.type = "button";
      button.dataset.action = "ar-filter";
      button.dataset.filter = key;
      button.setAttribute("aria-pressed", key === filter ? "true" : "false");
      button.append(document.createTextNode(name + " "), el("span", "ar-n", String(n)));
      filters.append(button);
    });
  }

  function signature(e) {
    return JSON.stringify([e.state, e.counts, e.query, e.why, e.site, e.site_title, e.url, e.title, e.chapters,
      (e.candidates || []).map((c) => c.url), working.has(e.key), e.how, e.by, e.tried, e.later]);
  }

  function showRows() {
    const shown = entries.filter((e) => matches(e, filter));
    const keep = new Set(shown.map((e) => e.key));
    rows.forEach((li, key) => { if (!keep.has(key)) { li.remove(); rows.delete(key); } });
    shown.forEach((e) => {
      let li = rows.get(e.key);
      const sig = signature(e);
      if (!li || li.dataset.sig !== sig) {
        const fresh = row(e);
        fresh.dataset.sig = sig;
        if (li) li.replaceWith(fresh);
        li = fresh;
        rows.set(e.key, li);
      }
      list.append(li);                        // in the list's order (moves only what is out of place)
    });
    empty.hidden = !!shown.length;
    empty.textContent = entries.length ? "" : "В папках манги и манхвы тайтлов нет — или папки не выбраны (вкладки " +
      "«Манга» и «Манхва», «Настройки → Хранение и копии»).";
  }

  function row(e) {
    const li = el("li", "ar-row");
    li.dataset.key = e.key;
    li.dataset.state = e.state;
    li.classList.toggle("busy", working.has(e.key));
    const cover = el(e.page ? "a" : "span", "ar-cover");     // (v5.44.84) a title outside the libraries: no page
    if (e.page) cover.href = e.page;
    cover.setAttribute("aria-hidden", "true");
    cover.tabIndex = -1;
    cover.append(el("span", "ar-initial", (e.title || "?").trim().charAt(0).toUpperCase()));
    const img = el("img");
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.onerror = () => img.remove();
    img.src = e.cover;
    cover.append(img);
    const main = el("div", "ar-main");
    const head = el("div", "ar-head-line");
    const title = el(e.page ? "a" : "span", "ar-title", e.title);
    if (e.page) title.href = e.page;
    head.append(title);
    if (e.kind) head.append(el("span", "ar-kind", e.kind === "manga" ? "Манга" : "Манхва"));
    const facts = [e.chapters > 1 || e.kind === "manhwa" ? plural(e.chapters, CHAPTERS) : plural(e.pages, PAGES),
      "папка «" + e.folder_name + "»"];
    if (e.found_as) facts.push(e.found_as);
    main.append(head, el("p", "ar-facts", facts.join(" · ")));
    // (v5.44.84) where it is in the parser's folder
    if (e.where) main.append(el("p", "ar-where", e.where));
    main.append(...stateBlock(e));
    li.append(cover, main);
    return li;
  }

  function button(text, action, primary) {
    const b = el("button", "ar-btn" + (primary ? " primary" : ""), text);
    b.type = "button";
    b.dataset.action = action;
    return b;
  }

  function again(e, hint) {
    const form = el("form", "ar-again");
    form.dataset.key = e.key;
    const input = el("input");
    input.type = "text";
    input.name = "q";
    input.placeholder = hint;
    input.setAttribute("aria-label", "Название для поиска");
    input.autocomplete = "off";
    input.spellcheck = false;
    const go = el("button", "ar-btn", "Найти");
    go.type = "submit";
    form.append(input, go);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const q = input.value.trim();
      if (q.length < 2) { input.focus(); return; }
      start(go, "/api/archive/recognize", { keys: [e.key], query: q }, [e.key], "Ищу «" + q + "»…");
    });
    return form;
  }

  function stateBlock(e) {
    const out = [];
    const tools = el("div", "ar-tools");
    if (e.state === "linked" || e.state === "own") {
      const site = e.site_title ? `«${e.site_title}»` : "";
      out.push(el("p", "ar-state ok", (e.state === "own" ? "Скачан программой" : "Узнан") + (site ? ": " + site : "")
        + (e.site ? ` (${siteLabel(e.site)})` : "")));
      if (e.state === "linked" && e.how && HOWS[e.how]) {
        out.push(el("p", "ar-how", "Как: " + HOWS[e.how] + (e.by && e.how !== "url" ? " — " + e.by : "")));
      }
      const c = e.counts;
      if (c) {
        out.push(el("p", "ar-count" + (c.missing ? " missing" : ""), `В папке ${c.have} из ${plural(c.total, CHAPTERS)}`
          + (c.missing ? ` · не хватает ${c.missing}` : " · все главы на месте")
          + (c.waiting ? ` · платных, которых нигде нет бесплатно: ${c.waiting}` : "")));
      } else {
        out.push(el("p", "ar-count", "Главы ещё не сверены — «Сверить главы узнанных»"));
      }
      if (e.url) {
        const get = el("a", "ar-btn" + (c && c.missing ? " primary" : ""), c && c.missing ? "Докачать" : "Главы");
        get.href = getPage(e);
        get.title = "Карточка тайтла с его главами: что есть в папке и чего нет";
        tools.append(get);
      }
      if (e.state === "linked") tools.append(button("Отвязать", "ar-unlink"));
    } else if (e.state === "choose") {
      out.push(el("p", "ar-state warn", (e.how === "pictures" && e.by ? `По картинкам похоже на ${e.by} — `
        + "проверьте и выберите, какой это тайтл" : "Похожих несколько — выберите, какой это тайтл")
        + (e.why ? ` (${e.why})` : "")));
      const cands = el("div", "ar-cands");
      (e.candidates || []).forEach((c) => cands.append(candidateCard(e, c)));
      out.push(cands);
      out.push(again(e, "Не то — другое название"));
    } else if (e.state === "none") {
      out.push(el("p", "ar-state bad", "Не нашлось на сайтах" + (e.why ? ` — ${e.why}` : "")));
      if ((e.tried || []).length) {
        const tried = el("ul", "ar-tried");
        tried.setAttribute("aria-label", "Что программа пробовала");
        e.tried.forEach((line) => tried.append(el("li", "", line)));
        out.push(tried);
      }
      if (e.later) out.push(el("p", "ar-how", "По картинкам программа спросит ещё раз сама, позже."));
      if ((e.candidates || []).length) {
        out.push(el("p", "ar-how", "Похожие — может, один из них:"));
        const cands = el("div", "ar-cands");
        e.candidates.forEach((c) => cands.append(candidateCard(e, c)));
        out.push(cands);
      }
      out.push(again(e, "Попробуйте другое название (по-английски, по-русски)"));
      tools.append(button("Искать снова", "ar-one"));
    } else {
      out.push(el("p", "ar-state", "Не проверен"));
      tools.append(button("Узнать", "ar-one", true));
    }
    // (v5.44.84) not a manga: the folder goes back to the booru parser
    if (e.state !== "own") tools.append(button("Это не манга", "ar-not-manga"));
    if (tools.children.length) out.push(tools);
    return out;
  }

  const SITES = { remanga: "Remanga", recomics: "ReComics", mangalib: "MangaLib", mangabuff: "MangaBuff",
    readmanga: "ReadManga", desu: "Desu", comx: "com-x.life", onelove: "Manga One Love", mangadex: "MangaDex",
    hentaichan: "Хентай-тян",
    hentailib: "HentaiLib", allhentai: "AllHentai", nudemoon: "Nude-Moon" };
  const siteLabel = (name) => SITES[name] || name;

  function candidateCard(e, c) {
    const card = el("button", "ar-cand");
    card.type = "button";
    card.dataset.action = "ar-pick";
    card.dataset.n = c.n;
    const pic = el("span", "ar-cand-cover");
    pic.append(el("span", "ar-initial", (c.title || "?").trim().charAt(0).toUpperCase()));
    if (c.cover) {
      const img = el("img");
      img.alt = "";
      img.loading = "lazy";
      img.onerror = () => img.remove();
      img.src = `/api/archive/cover?k=${encodeURIComponent(e.key)}&n=${c.n}`;
      pic.append(img);
    }
    const facts = [c.type, c.year || "", c.chapters ? plural(c.chapters, CHAPTERS) : ""].filter(Boolean).join(" · ");
    card.append(pic, el("span", "ar-cand-name", c.title || "Без названия"), el("span", "ar-cand-facts", facts),
      el("span", "ar-cand-sites", (c.sites || []).map((x) => x.label).join(", ")));
    card.title = "Это он — привязать" + ((c.alt || []).length ? ": " + c.alt.join(" / ") : "");
    return card;
  }

  // ── the work ──
  async function start(buttonEl, url, body, keys, words) {
    try {
      await postJson(url, body);
      (keys || []).forEach((k) => working.add(k));
      say(buttonEl, words || "");
      showRows();
      watcher.started();
    } catch (err) {
      say(buttonEl, err.message, true);
    }
  }

  function keyOf(node) {
    const li = node.closest(".ar-row");
    return li ? li.dataset.key : "";
  }

  actions["ar-recognize"] = (b) => start(b, "/api/archive/recognize", {},
    entries.filter((e) => e.state === "new").map((e) => e.key), "Узнаю тайтлы — по одному, это займёт время");
  actions["ar-counts"] = (b) => start(b, "/api/archive/counts", {}, [], "Сверяю главы узнанных…");
  actions["ar-download"] = (b) => start(b, "/api/archive/download", {}, [],
    "Ставлю в очередь скачивания — идёт в «Скачать → Манга» и «Манхва»");
  actions["ar-stop"] = (b) => postJson("/api/archive/stop", {}).then(() => say(b, "Останавливаю…"))
    .catch((err) => say(b, err.message, true));
  actions["ar-one"] = (b) => { const k = keyOf(b); if (k) start(b, "/api/archive/recognize", { keys: [k] }, [k], "Ищу…"); };
  actions["ar-pick"] = (b) => {
    const k = keyOf(b);
    if (k) start(b, "/api/archive/link", { key: k, n: Number(b.dataset.n) }, [k], "Привязываю…");
  };
  actions["ar-unlink"] = async (b) => {
    const k = keyOf(b);
    if (!k) return;
    if (LB.confirmed && !LB.confirmed(b, "Точно отвязать?")) return;
    try {
      await postJson("/api/archive/unlink", { key: k });
      refresh(false);
    } catch (err) {
      say(b, err.message, true);
    }
  };
  // (v5.44.84) «Проверить папку парсера» — the parser's whole folder looked at now
  actions["ar-scan"] = (b) => start(b, "/api/archive/scan", {}, [], "Смотрю папку парсера — в большой это долго");
  actions["ar-not-manga"] = async (b) => {
    const k = keyOf(b);
    if (!k) return;
    if (LB.confirmed && !LB.confirmed(b, "Отдать папку парсеру booru?")) return;
    try {
      await postJson("/api/archive/not-manga", { key: k });
      refresh(false);
    } catch (err) {
      say(b, err.message, true);
    }
  };
  actions["ar-filter"] = (b) => {
    filter = b.dataset.filter || "all";
    showFilters();
    showRows();
  };

  const watcher = LB.watchTask(job, "/api/archive/task", {
    onState(st, running) {
      const background = running && !!st.background;   // a round of the background: the buttons stop it and go
      mainButtons.forEach((b) => { b.disabled = running && !background; });
      stopButton.hidden = !running;
      job.classList.toggle("background", background);
      // the last line of its own (not the task's «ГОТОВО» / «ОСТАНОВЛЕНО»)
      const lines = (st.log || []).filter((l) => !/^[А-ЯЁ !]+$/.test(l.text));
      if (lines.length) lastLine = lines[lines.length - 1].text;
      step.classList.toggle("error", st.status === "failed");
      step.textContent = running ? lastLine : st.status === "failed" ? (st.failed || st.error || "Не вышло")
        : st.result && st.result.queued !== undefined ? (st.result.queued ? `В очередь скачивания: ${st.result.queued}. `
          + "Скачивание видно в «Скачать → Манга» и «Манхва»." : "Докачивать нечего — у узнанных все главы на месте.")
        : lastLine;
      if (running) refreshSoon(2500);
    },
    onEnd() {
      working.clear();
      say(mainButtons[0], "");
      refresh(false);
    },
  });

  refresh(false);
})();
