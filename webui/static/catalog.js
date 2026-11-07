/* Local Booru «Лента» of «Манга», «Манхва», «Видео» (v5.44.90, webui/pages/catalog.py, core/catalog.py).
   The filters are one GET form; this script only makes it pleasant: a genre or tag chip of three states (a click —
   «да», another — «нет», another — neither: hidden fields g/x, tg/tx), «все жанры», «Найти тег», «Порядок» applied at
   once, empty fields left out of the address; «… отвечает» and «Показать ещё» ask ?part=1 (JSON); (v5.44.103) back
   from a title's page — the list as it was left (sessionStorage «lb-feed-kept»). */
(() => {
  "use strict";
  const form = document.querySelector("form.cat");
  if (!form) return;

  const STATES = ["", "yes", "no"];
  const WORDS = { "": "неважно", yes: "да", no: "нет" };
  const MARKS = { "": "", yes: "✓", no: "−" };
  const sleep = (ms) => new Promise((go) => setTimeout(go, ms));
  const norm = (text) => String(text || "").toLocaleLowerCase("ru").replace(/ё/g, "е");

  // ── chips of three states ──
  function paint(chip) {
    const state = chip.dataset.state || "";
    const mark = chip.querySelector(".cat-mark");
    if (mark) mark.textContent = MARKS[state];
    chip.setAttribute("aria-label", `${chip.dataset.name}: ${WORDS[state]}`);
    chip.title = state ? `${WORDS[state]} — нажмите ещё раз, чтобы сменить` : "нажмите — да, ещё раз — нет";
  }

  function fields(tri) {
    const box = tri.querySelector(".cat-tri-fields");
    if (!box) return;
    box.replaceChildren();
    for (const chip of tri.querySelectorAll(".cat-tri-chip")) {
      const state = chip.dataset.state || "";
      if (!state) continue;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = state === "yes" ? tri.dataset.yes : tri.dataset.no;
      input.value = chip.dataset.name;
      box.append(input);
    }
  }

  for (const chip of form.querySelectorAll(".cat-tri-chip")) paint(chip);

  // a cover the site did not give — no broken picture, the tile's colour
  form.addEventListener("error", (event) => {
    if (event.target instanceof HTMLImageElement && event.target.closest(".cat-cover")) event.target.classList.add("gone");
  }, true);

  // ── what is clicked ──
  form.addEventListener("click", (event) => {
    const chip = event.target.closest(".cat-tri-chip");
    if (chip) {
      const now = STATES.indexOf(chip.dataset.state || "");
      chip.dataset.state = STATES[(now + 1) % STATES.length];
      paint(chip);
      fields(chip.closest(".cat-tri"));
      form.classList.add("changed");
      return;
    }
    const all = event.target.closest("[data-cat-all]");
    if (all) {
      const tri = all.closest(".cat-group").querySelector(".cat-tri");
      const open = !tri.classList.contains("open");
      tri.classList.toggle("open", open);
      all.setAttribute("aria-expanded", open ? "true" : "false");
      all.textContent = open ? "свернуть ‹" : "все жанры ›";
      return;
    }
    const more = event.target.closest("[data-cat-more]");
    if (more) {
      event.preventDefault();
      loadMore(more);
      return;
    }
    const head = event.target.closest(".cat-side h2");
    if (head && narrow.matches) head.closest(".cat-side").classList.toggle("closed");
  });

  form.addEventListener("change", (event) => {
    if (event.target.matches("[data-cat-order]")) {
      form.requestSubmit();
      return;
    }
    if (event.target.closest(".cat-side")) form.classList.add("changed");
  });

  // «Найти тег»: the tags whose names hold the words (the chosen ones are always there)
  const finder = form.querySelector("[data-cat-tagfind]");
  if (finder) {
    const tri = finder.closest(".cat-group").querySelector(".cat-tri");
    const limit = Number(finder.dataset.limit) || 24;
    // how well a name fits the words: 1 — it is them, 2 — it begins with them, 3 — it holds them (0 — not)
    const fit = (name, words) => (name === words ? 1 : name.startsWith(words) ? 2 : name.includes(words) ? 3 : 0);
    const look = () => {
      const words = norm(finder.value.trim());
      const found = [];
      for (const chip of tri.querySelectorAll(".cat-tri-chip[data-rest]")) {
        const rank = words ? fit(norm(chip.dataset.name), words) : 0;
        chip.classList.remove("found");
        chip.style.order = "";
        if (rank) found.push([rank, chip]);
      }
      found.sort((a, b) => a[0] - b[0]);
      for (const [rank, chip] of found.slice(0, limit)) {
        chip.classList.add("found");
        chip.style.order = String(rank);            // the closest first (the chosen ones stay before them)
      }
      finder.setAttribute("aria-description", words ? `найдено: ${Math.min(found.length, limit)}` : "");
    };
    finder.addEventListener("input", look);
    finder.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();                       // Enter here picks the closest tag found, not the whole form
      const best = [...tri.querySelectorAll(".cat-tri-chip.found")]
        .sort((a, b) => Number(a.style.order) - Number(b.style.order))[0];
      if (best) {
        best.click();
        finder.value = "";
        look();
      }
    });
  }

  // the address without the form's empty fields (and without both sites ticked — that is «both» anyway)
  form.addEventListener("submit", () => {
    for (const input of form.querySelectorAll("input[name], select[name]")) {
      if (input.type === "checkbox") continue;
      if (!String(input.value || "").trim()) input.disabled = true;
    }
    const sites = [...form.querySelectorAll('input[type="checkbox"][name="s"]')];
    if (sites.length && sites.every((box) => box.checked)) for (const box of sites) box.disabled = true;
  });
  window.addEventListener("pageshow", () => {
    for (const input of form.querySelectorAll("[disabled]")) input.disabled = false;
  });

  // the filters on a narrow window: folded under their heading
  const narrow = window.matchMedia("(max-width: 860px)");
  const side = form.querySelector(".cat-side");
  if (side && narrow.matches && !form.querySelector(".cat-picked")) side.classList.add("closed");

  // ── «… отвечает» and «Показать ещё» ──
  async function ask(address) {
    for (let round = 0; round < 40; round++) {
      try {
        const response = await fetch(address, { credentials: "same-origin" });
        const reply = await response.json();
        if (reply.pending) continue;               // the server waited for the sites: ask again at once
        return reply;
      } catch (_) {
        await sleep(3000);
      }
    }
    return null;
  }

  function moreLink(href) {
    const p = document.createElement("p");
    p.className = "cat-more";
    const a = document.createElement("a");
    a.className = "cat-btn";
    a.href = href;
    a.dataset.catMore = "";
    a.textContent = "Показать ещё";
    p.append(a);
    return p;
  }

  // (v5.44.98) a site that was late: the others' tiles are shown, its own are asked for and added to the grid
  function gridOf() {
    let grid = form.querySelector(".cat-grid");
    if (!grid) {
      grid = document.createElement("div");
      grid.className = "cat-grid";
      const empty = form.querySelector(".cat-empty");
      const more = form.querySelector(".cat-more");
      if (empty) empty.replaceWith(grid);
      else if (more) more.before(grid);
      else (form.querySelector(".cat-notes") || form.querySelector(".cat-main")).after(grid);
    }
    return grid;
  }

  function followLate(root) {
    (root || form).querySelectorAll(".cat-late[data-rest]:not([data-followed])").forEach(async (note) => {
      note.dataset.followed = "1";
      const reply = await ask(new URL(note.dataset.rest, location.href));
      if (!reply) {
        note.classList.add("bad");
        note.textContent = "Сайт так и не ответил — обновите страницу чуть позже.";
        return;
      }
      if (reply.html) gridOf().insertAdjacentHTML("beforeend", reply.html);
      const words = document.createElement("div");
      words.innerHTML = reply.notes || "";
      if (note.isConnected) note.replaceWith(...words.childNodes);
      if (reply.next && !form.querySelector(".cat-more")) gridOf().after(moreLink(reply.next));
      followLate(form);
    });
  }

  // (v5.44.103) back from a title's page («← Лента», the browser's «назад»): the list as it was left — the pages
  // «Показать ещё» glued, the place.  The program's pages are not kept by the browser (no-store): the feed comes anew,
  // its first page only — so the list is put aside when the page is left, and taken back on the way back.
  // (v5.44.107; the owner: «назад … в самое начало возвращается а не на то место где я был») each feed its own (the
  // feed of «Манга» and of «Манхва» both kept — 5.44.103 kept the last one only), for hours, not half an hour (a
  // chapter read before coming back), and taken back also when «← Лента» had to follow its link (the feed was not in
  // the history before the title: the reader's «К тайтлу» opened it anew) — title.js marks that («lb-feed-return»).
  const KEPT = "lb-feed-kept";               // {a feed's address: the list as it was left}
  const LAST = "lb-feed-last";               // {a feed's path: its address left last} — «← Лента» goes there
  const RETURN = "lb-feed-return";           // {path, at}: «← Лента» followed as a link a moment ago
  const KEEP_FEEDS = 4;
  const KEEP_FOR = 12 * 3600 * 1000;
  const here = location.pathname + location.search;
  function readKept() {
    let all = null;
    try { all = JSON.parse(sessionStorage.getItem(KEPT) || "null"); } catch (_) { all = null; }
    if (!all || typeof all !== "object" || Array.isArray(all)) return {};
    if (typeof all.url === "string" && typeof all.grid === "string") return { [all.url]: all };   // as 5.44.103 kept it
    return all;
  }
  function remembered(name) {
    try {
      const value = JSON.parse(sessionStorage.getItem(name) || "null");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (_) {
      return {};
    }
  }
  try {
    const last = remembered(LAST);
    last[location.pathname] = here;
    sessionStorage.setItem(LAST, JSON.stringify(last));
  } catch (_) { /* «← Лента» follows its own link */ }
  // the history's entry of this page says it is the feed: «← Лента» of a title's page goes back to it (the program
  // sends no Referer, and the history hides the addresses of its pages)
  try {
    const nav = window.navigation;
    if (nav && nav.currentEntry && typeof nav.updateCurrentEntry === "function") {
      const state = nav.currentEntry.getState();
      nav.updateCurrentEntry({ state: { ...(state && typeof state === "object" ? state : {}), lbFeed: location.pathname } });
    }
  } catch (_) { /* «← Лента» follows its link */ }
  // the covers' addresses, kept on the pictures themselves: leaving for another page of the program, motion.js takes
  // the address off every picture still coming (a cover below the window not loaded yet) — the list put aside then
  // would come back with empty covers
  function remember(root) {
    root.querySelectorAll("img[src]:not([data-cat-src])").forEach((img) => { img.dataset.catSrc = img.getAttribute("src"); });
  }
  remember(form);
  new MutationObserver((changes) => {
    for (const change of changes) {
      for (const node of change.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === "IMG") {
          if (node.hasAttribute("src") && !node.dataset.catSrc) node.dataset.catSrc = node.getAttribute("src");
        } else {
          remember(node);
        }
      }
    }
  }).observe(form, { childList: true, subtree: true });
  window.addEventListener("pagehide", () => {
    const grid = form.querySelector(".cat-grid");
    if (!grid || !grid.children.length) return;
    const copy = grid.cloneNode(true);
    copy.querySelectorAll("img[data-cat-src]").forEach((img) => {
      if (!img.getAttribute("src")) img.setAttribute("src", img.dataset.catSrc);
    });
    const more = form.querySelector("[data-cat-more]");
    const notes = form.querySelector(".cat-notes");
    const all = readKept();
    all[here] = { url: here, at: Date.now(), y: window.scrollY, grid: copy.innerHTML, page: grid.dataset.page || "",
      more: more ? more.getAttribute("href") : "", end: !!form.querySelector(".cat-end"), notes: notes ? notes.innerHTML : "" };
    let urls = Object.keys(all).filter((u) => all[u] && typeof all[u].grid === "string")
      .sort((a, b) => (all[b].at || 0) - (all[a].at || 0)).slice(0, KEEP_FEEDS);
    for (;;) {                                   // no room for them all: the oldest go first, this one stays
      const kept = {};
      urls.forEach((u) => { kept[u] = all[u]; });
      try {
        sessionStorage.setItem(KEPT, JSON.stringify(kept));
        break;
      } catch (_) {
        if (urls.length <= 1) {
          try { sessionStorage.removeItem(KEPT); } catch (__) { /* nothing put aside */ }
          break;
        }
        urls = urls.slice(0, -1);
      }
    }
  });
  (function takeBack() {
    const kept = readKept()[here];
    const nav = performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
    const asked = remembered(RETURN);           // «← Лента» followed its link a moment ago
    const called = asked.path === location.pathname && Date.now() - (Number(asked.at) || 0) < 20000;
    if (called) {
      try { sessionStorage.removeItem(RETURN); } catch (_) { /* it gets old by itself */ }
    }
    if (!kept || typeof kept.grid !== "string" || !kept.grid || !(called || (nav && nav.type === "back_forward"))
        || Date.now() - (Number(kept.at) || 0) > KEEP_FOR) return;
    const main = form.querySelector(".cat-main");
    if (!main) return;
    main.querySelectorAll(".cat-wait, .cat-empty, .cat-grid, .cat-more, .cat-end").forEach((node) => node.remove());
    let notes = main.querySelector(".cat-notes");
    if (!notes) {
      notes = document.createElement("div");
      notes.className = "cat-notes";
      main.append(notes);
    }
    notes.innerHTML = kept.notes || "";
    const grid = document.createElement("div");
    grid.className = "cat-grid";
    if (kept.page) grid.dataset.page = kept.page;
    grid.innerHTML = kept.grid;
    notes.after(grid);
    if (kept.more) {
      grid.after(moreLink(kept.more));
    } else if (kept.end) {
      const end = document.createElement("p");
      end.className = "cat-end";
      end.textContent = "Это всё.";
      grid.after(end);
    }
    try { history.scrollRestoration = "manual"; } catch (_) { /* the browser's own place, then ours */ }
    const place = () => window.scrollTo(0, kept.y || 0);
    place();
    requestAnimationFrame(place);
    window.addEventListener("load", place, { once: true });
    form.dataset.takenBack = "1";
  })();
  followLate(form);

  const wait = form.querySelector(".cat-wait");
  if (wait) {
    (async () => {
      const reply = await ask(new URL(wait.dataset.part, location.href));
      if (!wait.isConnected) return;
      if (!reply) {
        wait.classList.add("bad");
        wait.textContent = "Сайты так и не ответили — обновите страницу чуть позже.";
        return;
      }
      const parts = [];
      const notes = document.createElement("div");
      notes.className = "cat-notes";
      notes.innerHTML = reply.notes || "";
      parts.push(notes);
      if (reply.html || reply.rest) {
        const grid = document.createElement("div");
        grid.className = "cat-grid";
        grid.dataset.page = new URL(wait.dataset.part, location.href).searchParams.get("page") || "1";
        grid.innerHTML = reply.html || "";
        parts.push(grid);
      } else {
        const empty = document.createElement("p");
        empty.className = "cat-empty";
        empty.innerHTML = reply.empty || "Здесь пока ничего нет.";          // the server's words (with a link)
        parts.push(empty);
      }
      if (reply.next) parts.push(moreLink(reply.next));
      wait.replaceWith(...parts);
      followLate(form);
    })();
  }

  async function loadMore(link) {
    if (link.dataset.busy) return;
    link.dataset.busy = "1";
    link.setAttribute("aria-busy", "true");
    link.textContent = "Загружается…";
    const address = new URL(link.getAttribute("href"), location.href);
    address.searchParams.set("part", "1");
    const reply = await ask(address);
    delete link.dataset.busy;
    link.removeAttribute("aria-busy");
    if (!reply) {
      link.textContent = "Сайты не ответили — ещё раз";
      return;
    }
    let grid = form.querySelector(".cat-grid");
    if (!grid) {
      grid = document.createElement("div");
      grid.className = "cat-grid";
      link.closest(".cat-more").before(grid);
    }
    grid.insertAdjacentHTML("beforeend", reply.html || "");
    const notes = form.querySelector(".cat-notes");
    if (notes) {
      notes.innerHTML = reply.notes || "";
      followLate(notes);
    }
    if (reply.next) {
      link.href = reply.next;
      link.textContent = "Показать ещё";
    } else {
      const row = link.closest(".cat-more");
      const end = document.createElement("p");
      end.className = "cat-end";
      end.textContent = "Это всё.";
      row.replaceWith(end);
    }
  }
})();
