// Local Booru «Манхва» (v5.44.59, webui/pages/manhwa.py): a title's tabs, its chapters (the order, the ticks by hand);
// the reader — one strip: the place found again and kept, the chapter being read followed by the address and the bar,
// a chapter read to its end ticked, the next chapter coming by itself below, the bar hiding while read down.
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const actions = LB.actions || {};
  const postJson = LB.postJson || (async () => { throw new Error("нет связи со страницей"); });
  const say = LB.say || (() => {});

  // ── a title's page ────────────────────────────────────────────────────────────────────────────────────────────
  actions["mh-tab"] = (el) => {
    const page = el.closest(".mh-title");
    if (!page) return;
    const tab = el.dataset.tab;
    page.querySelectorAll('[role="tab"][data-tab]').forEach((b) => b.setAttribute("aria-selected", b === el ? "true" : "false"));
    page.querySelectorAll(".mh-pane[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
    const address = new URL(window.location.href);
    if (tab === "chapters") address.searchParams.set("tab", "chapters");
    else address.searchParams.delete("tab");
    history.replaceState(history.state, "", address.pathname + address.search);
  };

  // (v5.44.60) «Новые главы — скачивать самой» of a title «Скачать манхву» downloaded (not a page action: a click
  // action would keep the box from ticking); (v5.44.62) «брать с других сайтов» — the same way
  document.addEventListener("change", (event) => {
    const box = event.target;
    if (!(box instanceof HTMLInputElement) || !(box.dataset.follow || box.dataset.helpers)) return;
    const status = box.closest(".mh-source") && box.closest(".mh-source").querySelector(".action-status");
    const body = box.dataset.helpers ? { id: box.dataset.helpers, helpers: box.checked } :
      { id: box.dataset.follow, follow: box.checked };
    postJson("/api/manhwa/follow", body)
      .then((r) => say(status, box.dataset.helpers ?
        (r.helpers ? "Недостающие главы будут браться с других сайтов" : "С других сайтов главы не берутся") :
        (r.follow ? "Новые главы будут скачиваться сами" : "Новые главы сами не скачиваются")))
      .catch((err) => { box.checked = !box.checked; say(status, err.message, true); });
  });

  actions["mh-order"] = (el) => {
    const list = document.querySelector(".mh-chapters");
    if (!list) return;
    Array.from(list.children).reverse().forEach((li) => list.append(li));
    const newestFirst = list.dataset.order !== "desc";
    list.dataset.order = newestFirst ? "desc" : "asc";
    el.textContent = newestFirst ? "Сначала первые" : "Сначала новые";
  };

  actions["mh-read"] = async (el) => {
    const read = el.getAttribute("aria-pressed") !== "true";
    try {
      await postJson("/api/manhwa/read", { chapter: el.dataset.chapter, read });
      el.setAttribute("aria-pressed", read ? "true" : "false");
      el.title = read ? "Прочитана — снять отметку" : "Отметить прочитанной";
      const row = el.closest(".mh-ch");
      if (row) row.classList.toggle("read", read);
    } catch (err) {
      say(el, err.message, true);
    }
  };

  // (v5.44.90) «Лента» of the computer — webui/pages/catalog.py with static/catalog.js (the phone's has no script)

  actions["mh-reveal"] = async (el) => {
    try {
      await postJson("/api/manhwa/" + el.dataset.id + "/reveal");
      say(el, "Папка открыта");
    } catch (err) {
      say(el, err.message, true);
    }
  };

  // ── the reader ────────────────────────────────────────────────────────────────────────────────────────────────
  const reader = document.querySelector(".mh-reader");
  if (!reader) return;
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";     // the place is the reader's own
  const strip = reader.querySelector(".mh-strip");
  const bar = reader.querySelector("[data-bar]");
  const list = reader.querySelector(".mh-list");
  const where = reader.querySelector(".mh-bar-where");
  const tail = reader.querySelector("[data-tail]");
  const titleId = reader.dataset.title;
  const base = reader.dataset.base || "/manhwa/r/";   // (v5.44.66) the phone's reader: «/m/manhwa/r/»
  // (v5.44.82) a title read from a site without downloading (webui/pages/manga_online.py): its chapters and its place
  const chapterApi = reader.dataset.chapterApi || "/api/manhwa/chapter/";
  const progressApi = reader.dataset.progress || "/api/manhwa/progress";
  const KEEP = 4;                     // chapters kept in the page at once: the oldest goes when a fifth comes
  const AHEAD = 2.5;                  // the next chapter is asked for this many windows before the strip ends
  const done = new Set();             // chapters ticked in this visit
  let current = null;
  let loading = false;
  let saveTimer = 0;

  function sections() {
    return Array.from(strip.querySelectorAll(".mh-chapter[data-chapter]"));
  }

  function pagesOf(section) {
    return Array.from(section.querySelectorAll("img.mh-page"));
  }

  // where the strip is read: the chapter at the top of the window, its page there and how much of it is above
  function place() {
    for (const section of sections()) {
      const box = section.getBoundingClientRect();
      if (box.bottom <= 0) continue;
      const pages = pagesOf(section);
      let index = 0;
      let lo = 0;
      let hi = pages.length - 1;
      while (lo <= hi) {                                     // the last page whose top is above the window's top
        const mid = (lo + hi) >> 1;
        if (pages[mid].getBoundingClientRect().top <= 0) { index = mid; lo = mid + 1; } else { hi = mid - 1; }
      }
      const rect = pages[index] ? pages[index].getBoundingClientRect() : null;
      const part = rect && rect.height > 0 ? Math.min(1, Math.max(0, -rect.top / rect.height)) : 0;
      return { section, page: index, part };
    }
    return null;
  }

  function goTo(section, page, part) {
    const pages = pagesOf(section);
    const img = pages[Math.min(page, pages.length - 1)];
    if (!img) return;
    const rect = img.getBoundingClientRect();
    window.scrollTo(0, window.scrollY + rect.top + part * rect.height);
  }

  // the bar, the address and the title follow the chapter being read
  function follow(section) {
    if (section === current) return;
    current = section;
    const id = section.dataset.chapter;
    where.textContent = section.dataset.label;
    const setLink = (cls, target, rel, key, words) => {
      const link = bar.querySelector(cls);
      if (!link) return;
      if (target) {
        link.href = base + target;
        link.rel = rel;
        link.dataset.key = key;
        link.removeAttribute("aria-disabled");
        link.setAttribute("aria-label", words);
      } else {
        link.removeAttribute("href");
        link.removeAttribute("rel");
        delete link.dataset.key;
        link.setAttribute("aria-disabled", "true");
      }
    };
    setLink(".mh-bar-prev", section.dataset.prev, "prev", "ArrowLeft", "Предыдущая глава");
    setLink(".mh-bar-next", section.dataset.next, "next", "ArrowRight", "Следующая глава");
    list.querySelectorAll("li[data-chapter]").forEach((li) => li.classList.toggle("current", li.dataset.chapter === id));
    const name = document.title.split(" — ").slice(1).join(" — ");
    document.title = section.dataset.label + (name ? " — " + name : "");
  }

  function save(now, extra) {
    const spot = place();
    if (!spot) return;
    const body = Object.assign({ chapter: spot.section.dataset.chapter, page: spot.page, part: Number(spot.part.toFixed(4)) },
      extra || {});
    history.replaceState(history.state, "", base + body.chapter);
    if (now) {
      try {
        fetch(progressApi, { method: "POST", credentials: "same-origin", keepalive: true,
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      } catch (_) { /* the page is going away */ }
      return;
    }
    postJson(progressApi, body).catch(() => {});
  }

  function tick(section) {
    const id = section.dataset.chapter;
    if (done.has(id)) return;
    done.add(id);
    const pages = pagesOf(section);
    postJson(progressApi, { chapter: id, page: Math.max(0, pages.length - 1), part: 1, done: true })
      .catch(() => { done.delete(id); });
    const item = list.querySelector(`li[data-chapter="${id}"]`);
    if (item) item.classList.add("read");
  }

  // the next chapter, glued below the strip; the oldest one leaves (the place kept)
  async function more() {
    const sec = sections();
    const last = sec[sec.length - 1];
    if (loading || !last) return;
    const next = last.dataset.next;
    if (!next) {
      if (tail) tail.hidden = false;
      return;
    }
    loading = true;
    try {
      const response = await fetch(chapterApi + next, { credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "глава не открылась");
      const section = document.createElement("section");
      section.className = "mh-chapter";
      Object.assign(section.dataset, { chapter: data.id, label: data.label, prev: data.prev || "", next: data.next || "" });
      const head = document.createElement("h2");
      head.className = "mh-chapter-head";
      head.textContent = data.full;
      section.append(head);
      data.pages.forEach((p, n) => {
        const img = document.createElement("img");
        img.className = "mh-page";
        img.id = `c${data.id}-p${n + 1}`;
        img.alt = "";
        img.decoding = "async";
        if (n > 1) img.loading = "lazy";
        if (p.w > 0 && p.h > 0) { img.width = p.w; img.height = p.h; }
        img.src = p.src;
        section.append(img);
      });
      strip.append(section);
      while (sections().length > KEEP) {                       // the oldest leaves; what is read stays in its place
        const first = sections()[0];
        if (first === current) break;
        const before = sections()[1].getBoundingClientRect().top;
        first.remove();
        const after = sections()[0].getBoundingClientRect().top;
        window.scrollBy(0, after - before);
      }
    } catch (err) {
      say(where, "Следующая глава не открылась: " + err.message, true);
    } finally {
      loading = false;
    }
  }

  // ── scrolling: the bar, the place, the ticks, the next chapter ────────────────────────────────────────────────
  let lastY = window.scrollY;
  let pending = false;
  function onScroll() {
    pending = false;
    const y = window.scrollY;
    const dy = y - lastY;
    lastY = y;
    if (list.hidden) {
      if (dy > 4 && y > bar.offsetHeight + 40) bar.classList.add("mh-bar-away");
      else if (dy < -4 || y < 40) bar.classList.remove("mh-bar-away");
    }
    const spot = place();
    if (spot) follow(spot.section);
    for (const section of sections()) {                     // read to its end: its last page's bottom in the window
      const pages = pagesOf(section);
      const lastPage = pages[pages.length - 1];
      if (lastPage && lastPage.getBoundingClientRect().bottom <= window.innerHeight + 2 && window.scrollY > 0) tick(section);
    }
    const end = strip.getBoundingClientRect().bottom;
    if (end - window.innerHeight < AHEAD * window.innerHeight) more();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => save(false), 1200);
  }
  window.addEventListener("scroll", () => {
    if (!pending) {
      pending = true;
      requestAnimationFrame(onScroll);
    }
  }, { passive: true });
  window.addEventListener("pagehide", () => save(true));
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") save(true); });

  // ── the bar: the list of chapters, the width of the strip ────────────────────────────────────────────────────
  function openList(open) {
    list.hidden = !open;
    where.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      bar.classList.remove("mh-bar-away");
      const here = list.querySelector("li.current");     // the list's own scroll only: the strip stays where it is
      if (here) list.scrollTop = here.offsetTop - (list.clientHeight - here.offsetHeight) / 2;
    }
  }
  actions["mh-list"] = () => openList(list.hidden);
  document.addEventListener("click", (event) => {
    if (!list.hidden && !bar.contains(event.target)) openList(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !list.hidden) {
      openList(false);
      where.focus();
    }
  });

  actions["mh-width"] = (el) => {
    const spot = place();
    const width = el.dataset.width;
    reader.style.setProperty("--mh-w", width === "full" ? "100%" : width + "px");
    reader.dataset.width = width;
    bar.querySelectorAll("[data-action='mh-width']").forEach((b) => b.setAttribute("aria-pressed", b === el ? "true" : "false"));
    if (spot) goTo(spot.section, spot.page, spot.part);
    postJson("/api/prefs", { manhwa_width: width }).catch(() => {});
  };

  // ── the start: the place found again ─────────────────────────────────────────────────────────────────────────
  const first = sections()[0];
  if (first) {
    const page = Number(reader.dataset.page || 0);
    const part = Number(reader.dataset.part || 0);
    if (page > 0 || part > 0) goTo(first, page, part);
    follow(first);
  }
  lastY = window.scrollY;                       // the bar is shown on arrival, wherever the strip opens
  onScroll();
})();

// (v5.44.60) «Скачать манхву» (webui/pages/manhwa_get.py): a link → «Найти главы» (the title, its translators, its
// chapters, what the folder has) → «Скачать» → the title's page.  (v5.44.74) As «Скачать с сайта» of the videos
// (videos.js setupGet; the owner: «посмотри как сделано скачивание видео»): the card under its cover's own light, the
// chapters as tiles by tomes to tick, the bar at the bottom with «Скачать N глав» and the download in it — the
// chapter, its strips, the speed, «Пауза» / «Продолжить», «Остановить», the queue; each tile fills from the top as
// its strips come, the way a strip is read; a chapter that failed says when it is asked again.  The download's state
// brings only the chapters changed since the page's last question (since, run).
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const box = document.getElementById("manhwa-get");
  if (!box || !LB.watchTask) return;
  const actions = LB.actions || {};
  const postJson = LB.postJson;
  const say = LB.say || (() => {});
  const RUNNING = ["starting", "running", "stopping"];
  const form = box.querySelector('[data-form="manhwa-find"]');
  const field = form.querySelector('input[name="url"]');
  const findButton = form.querySelector('button[type="submit"]');
  const findJob = box.querySelector('[data-job="find"]');
  const step = findJob.querySelector(".mh-step");
  const foundBox = box.querySelector(".mh-found");
  const chaptersBox = foundBox.querySelector(".mh-chapters");
  const pickCount = foundBox.querySelector(".mh-pick-count");
  const goButton = foundBox.querySelector('[data-action="manhwa-get-go"]');
  const sum = foundBox.querySelector(".mh-dlbar-sum");
  const getJob = foundBox.querySelector('[data-job="download"]');
  const pauseButton = getJob.querySelector('[data-action="manhwa-get-pause"]');
  const stopButton = getJob.querySelector('[data-action="manhwa-get-stop"]');
  const meterBox = getJob.querySelector(".mh-meter");
  const resultBox = getJob.querySelector(".mh-result");
  const openBox = getJob.querySelector(".mh-open");
  const queueBox = getJob.querySelector(".mh-queue");
  // no title on the page (a download begun again after a restart, the nightly round, a title of before): the
  // download is shown on its own, under the search — in the title's bar again once a title is shown
  const going = box.querySelector(".mh-going");
  const goingNote = going.querySelector(".mh-going-note");
  const jobHome = getJob.parentElement;

  let findId = 0;                  // the search whose title is shown
  let found = null;
  let lastLine = "";
  let lastPicked = -1;             // shift+click: the tiles from this one to the clicked one
  let helpersBox = null;
  let runMine = false;             // the download going (or the last) is of this title
  let queueHere = null;            // this title's place in the queue (another download goes first)
  let runRun = 0;                  // the download whose chapters runItems has (core/manhwa_run: run)
  let runRev = 0;                  // …and its newest change the page has
  let runItems = new Map();        // a chapter's id → how its download goes
  let findWatch = null;
  let getWatch = null;

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
  const CHAPTERS = ["глава", "главы", "глав"];
  const CHAPTERS_TO = ["главу", "главы", "глав"];
  function size(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024 * 1024) return Math.max(1, Math.round(n / 1024)) + " КБ";
    return (n / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ";
  }
  function clock(seconds) {
    const m = Math.max(1, Math.round((Number(seconds) || 0) / 60));
    return m < 60 ? m + " мин" : Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "");
  }
  const downloading = () => RUNNING.includes(getJob.dataset.status || "");
  const busyHere = () => downloading() && runMine;
  const tiles = () => [...chaptersBox.querySelectorAll(".mh-tile")];
  const helpersOn = () => (helpersBox ? helpersBox.checked : !!(found && found.helpers_on !== false));
  const dayMonth = (date) => String(date || "").split(".").slice(0, 2).join(".");

  // ── the search: one line of what it does, its journal under «Подробности» ──
  function showStep(st, busy) {
    const lines = st.log || [];
    if (lines.length) lastLine = lines[lines.length - 1].text;
    step.classList.toggle("error", st.status === "failed");
    if (busy) step.textContent = lastLine || "Читаю тайтл…";
    else if (st.status === "failed") step.textContent = st.failed || st.error || "Не нашлось — проверьте ссылку";
    else if (st.status === "stopped") step.textContent = "Поиск остановлен";
    else step.textContent = "";
    step.hidden = !step.textContent;
  }

  function searching() {
    lastLine = "";
    step.textContent = "Читаю тайтл…";
    step.classList.remove("error");
    step.hidden = false;
    foundBox.hidden = true;
    findJob.hidden = false;
    watchFind().started();
  }

  async function find(url, branch) {
    if (!url) { say(findButton, "Вставьте ссылку на тайтл", true); field.focus(); return; }
    findButton.disabled = true;
    try {
      await postJson("/api/manhwa/find", { url, branch: branch || "" });
      say(findButton, "");
      searching();
    } catch (err) {
      findButton.disabled = false;
      say(findButton, err.message, true);
    }
  }

  // ── (v5.44.75) «Найти» by a name: the sites' searches at once, the titles as covers (core/manhwa_search.py);
  // a cover picked — its chapters looked for as by its link; the covers fold into one line above the title's card ──
  const hitsBox = box.querySelector(".mh-hits");
  const hitsTitle = hitsBox.querySelector("#mh-hits-title");
  const hitsSites = hitsBox.querySelector(".mh-hits-sites");
  const hitsList = hitsBox.querySelector(".mh-hits-list");
  const hitsFold = hitsBox.querySelector(".mh-hits-fold");
  const part = box.dataset.part === "manga" ? "manga" : "manhwa";
  const TITLES = ["тайтл", "тайтла", "тайтлов"];
  // the part's own kind first: «Скачать → Манга» — the manga (turned page by page), «Манхва» — the strips
  const GROUPS = part === "manga"
    ? [["manga", "Манга"], ["strip", "Манхва, маньхуа и другие"]]
    : [["strip", "Манхва, маньхуа и другие"], ["manga", "Манга"]];
  let hitsId = 0;                  // the search shown
  let hitsTimer = 0;
  let hitsQuery = "";
  let hitsCount = 0;
  let picked = "";                 // the address of the title picked (its chapters are looked for)
  const hitTiles = new Map();      // a title's address → its tile
  const isLink = (text) => /^https?:\/\/\S+$/i.test(text) || /^(?:www\.)?[\w-]+(?:\.[\w-]+)+\/\S*$/i.test(text);

  function searchName(text) {
    clearTimeout(hitsTimer);
    findButton.disabled = true;
    postJson("/api/manhwa/search", { q: text }).then((reply) => {
      findButton.disabled = false;
      say(findButton, "");
      startHits(reply.id, text);
      pollHits();
    }).catch((err) => {
      findButton.disabled = false;
      say(findButton, err.message, true);
    });
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
    hitsTitle.textContent = `Ищу «${text}»…`;
  }

  async function pollHits() {
    clearTimeout(hitsTimer);
    const asked = hitsId;
    try {
      const r = await fetch("/api/manhwa/search-state?id=" + asked, { credentials: "same-origin" });
      const st = await r.json();
      if (asked !== hitsId) return;
      showHits(st);
      if (!st.done) hitsTimer = setTimeout(pollHits, 500);
    } catch (err) {
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
    hitsTitle.textContent = !st.done
      ? `Ищу «${hitsQuery}»…` + (hitsCount ? " Пока " + plural(hitsCount, TITLES) : "")
      : hitsCount ? `По названию «${hitsQuery}» — ${plural(hitsCount, TITLES)}`
        : `По названию «${hitsQuery}» ничего не нашлось — попробуйте другое название (по-английски, по-русски) или ссылку`;
    hitsSites.textContent = "";
    (st.sites || []).forEach((x) => {
      const said = x.state === "asking" ? "…" : x.state === "failed" ? "не ответил" : String(x.count);
      const item = el("li", "mh-hits-site " + x.state);
      item.append(el("b", "", x.label), document.createTextNode(" " + said));
      if (x.error) item.title = x.error;
      hitsSites.append(item);
    });
    const seen = new Set();
    const lists = {};
    GROUPS.forEach(([key, name]) => {
      let group = hitsList.querySelector(`.mh-hits-group[data-group="${key}"]`);
      if (!group) {
        group = el("section", "mh-hits-group");
        group.dataset.group = key;
        group.append(el("h3", "", name), el("div", "mh-hits-grid"));
        hitsList.append(group);
      }
      lists[key] = { group, grid: group.querySelector(".mh-hits-grid"), name, tiles: [] };
    });
    Object.values(lists).forEach((x) => { x.tiles = []; });
    titles.forEach((t) => {
      lists[t.kind === "manga" ? "manga" : "strip"].tiles.push(hitTile(t));
      seen.add(t.url);
    });
    hitTiles.forEach((tile, key) => { if (!seen.has(key)) { tile.remove(); hitTiles.delete(key); } });
    Object.values(lists).forEach((x) => {
      // in the order of the answers; tiles moved only when it changed (a moved tile would lose the focus)
      const now = [...x.grid.children];
      if (now.length !== x.tiles.length || now.some((tile, k) => tile !== x.tiles[k])) x.grid.append(...x.tiles);
      x.group.hidden = !x.tiles.length;
      x.group.querySelector("h3").textContent = x.name + " · " + x.tiles.length;
    });
    if (picked && hitsBox.classList.contains("folded")) showFold();
  }

  function hitTile(t) {
    let tile = hitTiles.get(t.url);
    if (!tile) {
      tile = el("button", "mh-hit");
      tile.type = "button";
      tile.dataset.url = t.url;
      const cover = el("span", "mh-hit-cover");
      const initial = el("span", "mh-hit-initial", (t.title || "?").trim().charAt(0).toUpperCase());
      initial.setAttribute("aria-hidden", "true");
      cover.append(initial);
      tile.append(cover, el("span", "mh-hit-name"), el("span", "mh-hit-alt"), el("span", "mh-hit-facts"),
        el("span", "mh-hit-sites"));
      tile.addEventListener("click", () => pickHit(tile.dataset.url));
      hitTiles.set(t.url, tile);
    }
    tile.hit = t;
    const cover = tile.querySelector(".mh-hit-cover");
    if ((t.cover || "") !== (tile.dataset.cover || "")) {      // a site with a cover has answered: its cover
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
    let badge = cover.querySelector(".mh-hit-badge");
    if (t.adult && !badge) cover.append(el("span", "mh-hit-badge", "18+"));
    else if (!t.adult && badge) badge.remove();
    tile.querySelector(".mh-hit-name").textContent = t.title || "Без названия";
    const alt = tile.querySelector(".mh-hit-alt");
    alt.textContent = (t.alt || [])[0] || "";
    alt.hidden = !alt.textContent;
    const facts = [t.type, t.year || "", t.chapters ? plural(t.chapters, CHAPTERS) : "",
      t.rating ? "★ " + String(t.rating).replace(".", ",") : ""].filter(Boolean);
    tile.querySelector(".mh-hit-facts").textContent = facts.join(" · ");
    tile.querySelector(".mh-hit-sites").textContent = (t.sites || []).map((x) => x.label).join(", ");
    const on = t.url === picked;
    tile.classList.toggle("picked", on);
    tile.setAttribute("aria-pressed", on ? "true" : "false");
    tile.setAttribute("aria-label", [t.title, ...facts, "на " + (t.sites || []).map((x) => x.label).join(", ")]
      .filter(Boolean).join(", "));
    return tile;
  }

  function showFold() {
    const tile = hitTiles.get(picked);
    const name = tile && tile.hit ? tile.hit.title : "";
    hitsFold.hidden = false;
    hitsFold.querySelector("button").textContent = `Вернуться к найденному по «${hitsQuery}» — ${plural(hitsCount, TITLES)}`;
    hitsFold.title = name ? "Сейчас открыт «" + name + "»" : "";
  }

  function pickHit(url) {
    const tile = hitTiles.get(url);
    if (!tile || !tile.hit) return;
    picked = url;
    hitTiles.forEach((x, key) => {
      x.classList.toggle("picked", key === url);
      x.setAttribute("aria-pressed", key === url ? "true" : "false");
    });
    hitsBox.classList.add("folded");
    showFold();
    find(url, "");
    hitsBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  actions["manhwa-hits-all"] = () => {
    hitsBox.classList.remove("folded");
    hitsFold.hidden = true;
    const tile = hitTiles.get(picked);
    (tile || hitsBox).scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (tile) tile.focus({ preventScroll: true });
  };

  // ── the title's card ──
  function showCard() {
    const f = found;
    foundBox.querySelector(".mh-found-head").textContent = "Нашлось на " + (f.site_label || "сайте");
    foundBox.querySelector(".mh-found-title").textContent = f.title || "Без названия";
    const alt = foundBox.querySelector(".mh-found-alt");
    alt.textContent = (f.alt || []).join(" / ");
    alt.hidden = !alt.textContent;
    const facts = foundBox.querySelector(".mh-found-facts");
    facts.textContent = "";
    [f.type, f.year, f.status, f.age].filter(Boolean).forEach((x) => facts.append(el("li", "", String(x))));
    (f.genres || []).forEach((g) => facts.append(el("li", "genre", g)));
    facts.hidden = !facts.children.length;
    const about = foundBox.querySelector(".mh-found-about");     // a few lines of its description; a click — all
    about.textContent = f.about || "";
    about.hidden = !f.about;
    about.classList.remove("open");
    // its cover: on the card, and its colours as the light under the card's head
    const img = foundBox.querySelector(".mh-fcard-cover img");
    const initial = foundBox.querySelector(".mh-fcard-initial");
    initial.textContent = (f.title || "?").trim().charAt(0).toUpperCase();
    foundBox.querySelector(".mh-fcard-cover").title = f.cover_hidden ? "Обложка скрыта (SFW)" : "";
    img.hidden = true;
    img.removeAttribute("src");
    foundBox.classList.remove("has-cover");
    foundBox.style.removeProperty("--mh-cover");
    if (f.cover) {
      img.onload = () => {
        img.hidden = false;
        foundBox.style.setProperty("--mh-cover", 'url("' + img.src + '")');
        foundBox.classList.add("has-cover");
      };
      img.onerror = () => { img.hidden = true; };
      img.src = "/api/manhwa/find-cover?find=" + encodeURIComponent(findId);
    }
    // (v5.44.72) the card's own words first: a link of a site that lets no program in, a list of chapters not given
    const notes = foundBox.querySelector(".mh-found-notes");
    notes.textContent = "";
    (f.notes || []).forEach((note) => notes.append(el("p", "mh-found-note", note)));
    showChoice();
  }

  function showChoice() {
    const f = found;
    const choice = foundBox.querySelector(".mh-found-choice");
    choice.textContent = "";
    helpersBox = null;
    if (f.branches && f.branches.length > 1) {
      const label = el("label", "mh-get-branch");
      label.append(el("span", "", f.languages ? "Язык глав" : "Переводчики"));
      const select = el("select");
      f.branches.forEach((b) => {
        // (v5.44.79) MangaDex's branches are languages: no votes, nothing to say of them
        const option = el("option", "", `${b.name || "без названия"} — ${plural(b.count, CHAPTERS)}` +
          (b.votes ? ", " + plural(b.votes, ["оценка", "оценки", "оценок"]) : ""));
        option.value = b.id;
        option.selected = b.id === f.branch;
        select.append(option);
      });
      select.addEventListener("change", () => find(f.url, select.value));
      label.append(select);
      choice.append(label);
    } else if (f.branches && f.branches.length === 1 && f.branches[0].name) {
      choice.append(el("p", "mh-get-team", "Переводчики: " + f.branches[0].name));
    }
    const where = el("fieldset", "mh-get-where-pick");
    where.append(el("legend", "", "Куда"));
    [["manhwa", "Манхва — лентой"], ["manga", "Манга — по страницам"]].forEach(([value, text]) => {
      const label = el("label");
      const radio = el("input");
      radio.type = "radio";
      radio.name = "mh-where";
      radio.value = value;
      radio.checked = f.kind === value;
      radio.disabled = value === "manga" && !(f.roots && f.roots.manga);
      label.append(radio, document.createTextNode(" " + text));
      if (radio.disabled) label.title = "Папка манги не выбрана — «Настройки → Папки»";
      where.append(label);
    });
    choice.append(where);
    const followLabel = el("label", "mh-get-follow");
    const followBox = el("input");
    followBox.type = "checkbox";
    followBox.checked = true;
    followBox.name = "follow";
    followLabel.append(followBox, document.createTextNode(" Новые главы — скачивать самой (раз в сутки, ночью)"));
    choice.append(followLabel);
    // (v5.44.62) the other sites with this title: the chapters its own site has not free, from them
    const giving = (f.helpers || []).filter((h) => h.gives > 0);
    if (giving.length) {
      const block = el("div", "mh-get-helpers");
      const label = el("label", "mh-get-follow");
      helpersBox = el("input");
      helpersBox.type = "checkbox";
      helpersBox.name = "helpers";
      helpersBox.checked = f.helpers_on !== false;
      label.append(helpersBox, document.createTextNode(` Главы, которых нет бесплатно на ${f.site_label}, — брать с ` +
        "других сайтов"));
      const list = el("ul", "mh-get-helper-list");
      giving.forEach((h) => list.append(el("li", "", `${h.site_label} — «${h.title}»: ` +
        plural(h.gives, CHAPTERS) + (h.first ? ` (${h.first}${h.last !== h.first ? " — " + h.last : ""})` : ""))));
      block.append(label, list);
      helpersBox.addEventListener("change", () => {
        paintBase(helpersBox.checked ? "on" : "off");
        paintRun();
        showPaid();
        countChosen();
      });
      choice.append(block);
    } else if (f.helpers && f.helpers.length) {
      choice.append(el("p", "mh-get-team", "Тайтл есть и на " + f.helpers.map((h) => h.site_label).join(", ") +
        " — новых глав оттуда сейчас нет"));
    }
  }

  // the paid chapters: which of them come from the other sites, which wait (and for what)
  function showPaid() {
    const f = found;
    const paid = foundBox.querySelector(".mh-found-paid");
    const others = helpersOn() && !!helpersBox;
    const left = others ? f.paid_left : f.paid_count;
    const covered = others ? f.paid_count - f.paid_left : 0;
    const soonOf = (list) => list.slice(0, 3).map((c) => c.label + (c.free_at ? " — " + c.free_at : "")).join("; ");
    // (v5.44.73) a paid chapter with no date to become free (Remanga's licensed ones): it waits for another site
    const dated = (list) => (list || []).some((c) => c.free_at);
    const nowhere = "бесплатно их пока нет ни на одном из сайтов — появятся, программа скачает сама.";
    let words = "";
    if (f.paid_count && covered && !left) {
      words = `Платных на ${f.site_label}: ${f.paid_count} — все есть бесплатно на других сайтах, скачаются оттуда.`;
    } else if (f.paid_count && covered) {
      const soon = soonOf(f.waiting || []);
      words = `Платных на ${f.site_label}: ${f.paid_count}, из них ${covered} есть бесплатно на других сайтах. ` +
        (dated(f.waiting) ? `Остальные программа возьмёт сама, когда сайт сделает их бесплатными${soon ? ": " +
        soon + (left > 3 ? "…" : "") : ""}.` : `Остальные ${left} — ${nowhere}`);
    } else if (f.paid_count) {
      const soon = soonOf(f.paid || []);
      words = `Платных: ${f.paid_count} — ` + (dated(f.paid) ? `их программа не скачивает, возьмёт сама, когда ` +
        `сайт сделает их бесплатными${soon ? ": " + soon + (f.paid_count > 3 ? "…" : "") : ""}.` : nowhere);
    }
    paid.textContent = words;
    paid.hidden = !words;
  }

  // ── the chapters as tiles, by tomes ──
  // a chapter as the folder and the sites have it now: can it be ticked, what its tile says
  function base(c) {
    if (c.have) return { ok: false, mark: "есть", kind: "have" };
    const date = c.free_at ? " · до " + dayMonth(c.free_at) : "";
    if (c.wait) return { ok: false, mark: "платная" + date, kind: "wait" };
    if (c.from) {
      if (helpersOn()) return { ok: true, mark: "с " + c.from, kind: "from" };
      return { ok: false, mark: c.paid ? "платная" + date : "только на " + c.from, kind: "wait" };
    }
    return { ok: true, mark: "", kind: "" };
  }

  // (v5.44.82) a chapter read without downloading it (webui/pages/manga_online.py): its place in the card's list
  const onlineHref = (n, extra) => "/online?url=" + encodeURIComponent(found.url)
    + (found.branch ? "&branch=" + encodeURIComponent(found.branch) : "") + "&ch=" + n + (extra || "");

  // the program's line icon «play» (webui/icons.py PLAY): not a sign of the text — the tile's words stay its own
  function playIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    for (const [k, v] of Object.entries({ class: "icon i-play", viewBox: "0 0 24 24", width: "14", height: "14",
      fill: "none", stroke: "currentColor", "stroke-width": "1.75", "stroke-linecap": "round", "stroke-linejoin": "round",
      "aria-hidden": "true", focusable: "false" })) svg.setAttribute(k, v);
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M7 4.5v15l12-7.5z");
    svg.append(path);
    return svg;
  }

  function tile(c, n) {
    const label = el("label", "mh-tile");
    label.dataset.id = c.id;
    const tick = el("input");
    tick.type = "checkbox";
    tick.value = c.id;
    label.append(tick, el("span", "mh-tile-n", c.n || "•"), el("span", "mh-tile-t", c.name || ""),
      el("span", "mh-tile-mark", ""));
    tick.setAttribute("aria-label", c.label + (c.name ? " — " + c.name : ""));
    if (!c.wait) {                                   // (v5.44.82) «Читать» — not a paid one nobody has free
      const read = el("a", "mh-tile-read");
      read.append(playIcon());
      read.href = onlineHref(n);
      read.title = "Читать без скачивания";
      read.setAttribute("aria-label", "Читать «" + c.label + "» без скачивания");
      label.append(read);
    }
    return label;
  }

  // (v5.44.82) «Читать с начала», «Продолжить: Глава 12» — the place of the title read without downloading it
  function showRead() {
    const tools = foundBox.querySelector(".mh-read-tools");
    if (!tools) return;
    tools.textContent = "";
    const list = found.list || [];
    const first = list.findIndex((c) => !c.wait);
    if (first < 0) return;
    const start = el("a", "mh-read-start", "Читать с начала");
    start.href = onlineHref(first + 1, "&from=start");
    tools.append(start);
    const asked = found.url;
    fetch("/api/online/last?url=" + encodeURIComponent(found.url), { credentials: "same-origin" })
      .then((r) => r.json()).then((reply) => {
        const last = reply && reply.last;
        if (!last || !last.ch || !found || found.url !== asked) return;
        const n = last.done && last.ch < list.length ? last.ch + 1 : last.ch;
        const go = el("a", "mh-read-go", "Продолжить: " + ((list[n - 1] && list[n - 1].label) || last.label));
        go.href = onlineHref(n);
        tools.prepend(go);
      }).catch(() => {});
  }

  function showTiles() {
    chaptersBox.textContent = "";
    const list = found.list || [];
    const place = new Map(list.map((c, i) => [c, i + 1]));      // (v5.44.82) a chapter's place: «Читать»
    const tomes = new Map();
    list.forEach((c) => {
      const key = Number(c.tome) || 0;
      if (!tomes.has(key)) tomes.set(key, []);
      tomes.get(key).push(c);
    });
    const many = tomes.size > 1;
    for (const [tome, chapters] of tomes) {
      const section = el("section", "mh-tome");
      if (many) {
        const head = el("div", "mh-tome-head");
        head.append(el("h4", "", tome ? "Том " + tome : "Без тома"), el("span", "mh-tome-count", plural(chapters.length, CHAPTERS)));
        const toggle = el("button", "link", "отметить все");
        toggle.type = "button";
        toggle.dataset.tome = String(tome);
        head.append(toggle);
        section.append(head);
      }
      const grid = el("div", "mh-tiles");
      chapters.forEach((c) => grid.append(tile(c, place.get(c))));
      section.append(grid);
      chaptersBox.append(section);
    }
    if (!list.length) chaptersBox.append(el("p", "mh-pick-empty", "Глав у тайтла нет ни на одном из сайтов."));
  }

  // the tiles as the folder and the boxes have them; ``tick``: "all" — every one that can be had ticked (a new list),
  // "on" / "off" — the other sites' chapters ticked / let go (their box)
  function paintBase(tick) {
    const byId = new Map((found.list || []).map((c) => [String(c.id), c]));
    for (const t of tiles()) {
      const c = byId.get(t.dataset.id);
      if (!c) continue;
      const b = base(c);
      const input = t.querySelector("input");
      const was = input.disabled;
      input.disabled = !b.ok;
      if (!b.ok) input.checked = false;
      else if (tick === "all" || (tick === "on" && b.kind === "from" && was)) input.checked = true;
      if (b.kind) t.dataset.base = b.kind; else delete t.dataset.base;
      t.dataset.mark = b.mark;
      t.dataset.tip = [c.label + (c.name ? " — " + c.name : ""), c.from && b.ok ? "Скачается с " + c.from : "",
        b.kind === "wait" ? (c.free_at ? "Платная — бесплатной станет " + c.free_at
          : c.paid ? "Платная — бесплатно её пока нет ни на одном из сайтов" : "Есть только на " + c.from) : "",
        b.kind === "have" ? "Уже в папке" : ""].filter(Boolean).join("\n");
      t.title = t.dataset.tip;
    }
  }

  // the download of this title on its tiles: each one's state, its strips as the share filled from the top
  const RUN_MARKS = { wait: "в очереди", queued: "в очереди", going: "качается", paused: "пауза", retry: "повтор",
    done: "готово", failed: "не скачалась" };
  function paintRun() {
    const queued = new Set(queueHere ? (queueHere.chosen || []).map(String) : []);
    for (const t of tiles()) {
      const mark = t.querySelector(".mh-tile-mark");
      const x = runMine ? runItems.get(t.dataset.id) : null;
      let state = x ? x.state : queued.has(t.dataset.id) ? "queued" : "";
      if (state === "wait" && !busyHere()) state = "";       // stopped before its turn: as it was
      let share = 0;
      let text = t.dataset.mark || "";
      if (state === "going" || state === "paused") {
        share = x.pages ? Math.min(1, x.got / x.pages) : 0;
        text = RUN_MARKS[state] + (x.pages ? " " + Math.floor(share * 100) + "%" : "");
      } else if (state === "retry") {
        text = "повтор" + (x.again > 0 ? " через " + x.again + " с" : "…");
      } else if (state) {
        text = RUN_MARKS[state] || state;
      }
      if (state) t.dataset.dl = state; else delete t.dataset.dl;
      if (mark.textContent !== text) mark.textContent = text;
      const fill = Math.round(share * 100) + "%";
      if (t.style.getPropertyValue("--p") !== fill) t.style.setProperty("--p", fill);
      const why = x && (state === "failed" || state === "retry") && x.why
        ? "Не скачалась: " + x.why + (x.tries ? " (повтор " + x.tries + " из 3)" : "") : "";
      const tip = (t.dataset.tip || "") + (why ? "\n" + why : "");
      if (t.title !== tip) t.title = tip;
    }
  }

  // another title downloads now: this one goes after it
  const goWord = () => (downloading() && !runMine ? "В очередь: " : "Скачать ");

  function chosenIds() {
    return tiles().map((t) => t.querySelector("input")).filter((i) => i.checked && !i.disabled).map((i) => i.value);
  }

  function countChosen() {
    if (!found) return;
    const inputs = tiles().map((t) => t.querySelector("input"));
    const open = inputs.filter((i) => !i.disabled).length;
    const chosen = chosenIds().length;
    const have = (found.list || []).filter((c) => c.have).length;
    const waiting = (found.list || []).filter((c) => base(c).kind === "wait").length;
    goButton.disabled = busyHere() || (!chosen && !(open === 0 && waiting));
    const waits = !chosen && !open && waiting;          // nothing to take now: the title kept, its paid ones awaited
    goButton.textContent = chosen ? goWord() + plural(chosen, CHAPTERS_TO)
      : open ? "Отметьте главы" : waits ? "Ждать платные" : "Всё уже скачано";
    const folderName = String(found.folder || "").split(/[\\/]/).filter(Boolean).pop() || "";
    sum.textContent = (waits ? "программа возьмёт их сама, когда будет можно, — " : "") + "в папку «" + folderName + "»";
    sum.title = String(found.folder || "");
    pickCount.textContent = [open ? "отмечено " + chosen + " из " + open : "", have ? "в папке: " + have : "",
      waiting ? "платных ждут: " + waiting : ""].filter(Boolean).join(" · ");
    foundBox.classList.toggle("all-had", !open && !waiting);
  }

  function showFound(st) {
    if (!st || !st.found) { foundBox.hidden = true; return; }
    found = st.found;
    if (st.id !== findId) {          // another title: the download going is not its until the state says so
      runMine = false;
      queueHere = null;
      if (getWatch) getWatch.poll();
    }
    findId = st.id;
    foundBox.hidden = false;
    lastPicked = -1;
    showCard();
    showTiles();
    showRead();
    paintBase("all");
    paintRun();
    showPaid();
    countChosen();
  }

  // ── the download: the tiles show each chapter's way, the bar the whole of it ──
  function showMeter(m, mine) {
    if (!m || !m.total) { meterBox.hidden = true; return; }
    meterBox.hidden = false;
    const now = m.now;
    const part = now && now.pages ? Math.min(1, now.got / now.pages) : 0;
    const whole = Math.min(1, ((m.done || 0) + (m.failed || 0) + part) / m.total);
    meterBox.querySelector("progress").value = Math.round(whole * 1000);
    meterBox.querySelector(".mh-meter-pct").textContent = Math.floor(whole * 100) + "%";
    const bits = [];
    if (now) {
      bits.push((m.paused ? "Пауза — " : "") + now.label + (now.from ? " (с " + now.from + ")" : "")
        + (now.pages ? ": " + now.got + " из " + now.pages + " " + word(now.pages, ["полосы", "полос", "полос"]) : ""));
    } else if (m.paused) {
      bits.push("Пауза");
    }
    if (m.speed && !m.paused) bits.push(size(m.speed) + "/с");
    if (m.eta && !m.paused) bits.push("осталось ≈ " + clock(m.eta));
    const tail = "Всего: " + (m.done || 0) + " из " + m.total + (m.retrying ? ", ждут повтора: " + m.retrying : "")
      + (m.failed ? ", не скачались: " + m.failed : "");
    meterBox.querySelector(".mh-meter-text").textContent = (!mine && m.title ? "«" + m.title + "»: " : "")
      + (bits.length ? bits.join(" · ") + ". " : "") + tail;
  }

  function showResult(st, busy) {
    const r = st.result || {};
    if (busy || !st.id || !(runMine || foundBox.hidden) || !["done", "stopped", "failed"].includes(st.status)) {
      resultBox.hidden = true;
      openBox.hidden = true;
      return;
    }
    const got = Number(r.got) || 0;
    const failed = (r.failed || []).length;
    let text = st.status === "failed" ? (st.error || "Скачивание прервалось — причина в подробностях")
      : (st.status === "stopped" || r.stopped ? "Остановлено: " : "Готово: ")
        + (got ? "скачано " + plural(got, CHAPTERS) : "новых глав не скачано");
    if (st.status !== "failed" && failed) text += ", не скачались: " + failed + " — причины в подробностях";
    if (st.status !== "failed" && r.gave_up) text += ". " + r.gave_up;
    resultBox.textContent = text;
    resultBox.className = "mh-result " + (st.status === "failed" || (failed && !got) || r.gave_up ? "bad"
      : failed || r.stopped || st.status === "stopped" ? "part" : "ok");
    resultBox.hidden = false;
    openBox.hidden = !r.open;
    if (r.open) openBox.querySelector("a").href = r.open;
  }

  function showQueue(queue) {
    queueBox.textContent = "";
    queueBox.hidden = !queue.length;
    queue.forEach((q, n) => {
      const here = String(q.find) === String(findId);
      const li = el("li", here ? "here" : "");
      li.append(el("span", "mh-queue-what", (n ? "Потом" : "Дальше") + ": «" + q.title + "»"
        + (q.count ? " — " + plural(q.count, CHAPTERS) : "") + (here ? " (этот тайтл)" : "")));
      const drop = el("button", "link mh-queue-drop", "убрать");
      drop.type = "button";
      drop.addEventListener("click", () => postJson("/api/manhwa/download/unqueue", { id: q.id })
        .then(() => { queueHere = null; watchGet().poll(); }).catch((err) => say(drop, err.message, true)));
      li.append(drop);
      queueBox.append(li);
    });
  }

  function placeJob() {
    const alone = foundBox.hidden;
    if (alone && getJob.parentElement !== going) going.append(getJob);
    if (!alone && getJob.parentElement !== jobHome) jobHome.append(getJob);
    going.hidden = !alone || (getJob.hidden && goingNote.hidden);
  }
  new MutationObserver(placeJob).observe(foundBox, { attributes: true, attributeFilter: ["hidden"] });
  new MutationObserver(placeJob).observe(getJob, { attributes: true, attributeFilter: ["hidden"] });

  // the downloads of the last run of the program, before they are begun again (some seconds after its start)
  function showRestoring(st) {
    const soon = st.restoring || null;
    goingNote.hidden = !soon;
    if (soon) {
      goingNote.textContent = "С прошлого запуска программы продолжится: "
        + soon.titles.map((t) => "«" + t + "»").join(", ") + (soon.in > 0 ? " — через " + soon.in + " с" : " — сейчас");
    }
    placeJob();
  }

  function watchFind() {
    findWatch = findWatch || LB.watchTask(findJob, () => "/api/manhwa/find-state?seen=" + (found ? findId : ""), {
      onState(st, busy) {
        findButton.disabled = busy;
        showStep(st, busy);
        if (!busy && st.found && st.id !== findId) showFound(st);
        if (busy) foundBox.hidden = true;
      },
    });
    return findWatch;
  }

  function watchGet() {
    getWatch = getWatch || LB.watchTask(getJob, () => "/api/manhwa/download-state?since=" + runRev + "&run=" + runRun, {
      onState(st, busy) {
        const m = st.meter || {};
        if ((m.run || 0) !== runRun) { runItems = new Map(); runRun = m.run || 0; runRev = 0; }
        (st.items || []).forEach((x) => runItems.set(String(x.id), x));
        runRev = Math.max(runRev, m.rev || 0);
        // a download of another title (or the nightly round) is not this title's: its tiles stay as they are, the
        // bar says what goes; this title waiting in the queue — its tiles «в очереди»
        const queue = st.queue || [];
        const mine = !!st.id && st.find !== null && st.find !== undefined && String(st.find) === String(findId);
        runMine = mine;
        queueHere = queue.find((q) => String(q.find) === String(findId)) || null;
        foundBox.classList.toggle("run-other", !!st.id && !mine);
        foundBox.classList.toggle("run-busy", busy);
        foundBox.classList.toggle("downloading", busy && mine);
        stopButton.hidden = !busy;
        stopButton.textContent = busy && queue.length ? "Остановить всё" : "Остановить";
        pauseButton.hidden = !busy || st.status === "stopping";
        pauseButton.textContent = st.paused ? "Продолжить" : "Пауза";
        pauseButton.classList.toggle("primary", !!st.paused);
        getJob.classList.toggle("paused", busy && !!st.paused);
        showMeter(busy ? m : null, mine);
        paintRun();
        showQueue(queue);
        showResult(st, busy);
        showRestoring(st);
        countChosen();
      },
      onEnd() {
        meterBox.hidden = true;
        say(stopButton, "");
        // the list shows what is in the folder now (the chapters downloaded are «есть»)
        fetch("/api/manhwa/find-state", { credentials: "same-origin" }).then((r) => r.json())
          .then((st) => { if (st.found && st.id === findId) showFound(st); }).catch(() => {});
      },
    });
    return getWatch;
  }

  foundBox.querySelector(".mh-found-about").addEventListener("click", (event) => {
    event.currentTarget.classList.toggle("open");
  });

  // ticking: a tile, shift+click — the tiles between, a tome's «отметить все», «все» / «ничего»
  chaptersBox.addEventListener("click", (event) => {
    const toggle = event.target.closest("button[data-tome]");
    if (toggle) {
      event.preventDefault();
      const inputs = [...toggle.closest(".mh-tome").querySelectorAll(".mh-tile input")].filter((i) => !i.disabled);
      const on = inputs.some((i) => !i.checked);
      inputs.forEach((i) => { i.checked = on; });
      countChosen();
      return;
    }
    const tick = event.target.closest(".mh-tile input");
    if (!tick) return;
    const all = tiles().map((t) => t.querySelector("input"));
    const at = all.indexOf(tick);
    if (event.shiftKey && lastPicked >= 0 && lastPicked !== at) {
      const [from, to] = at < lastPicked ? [at, lastPicked] : [lastPicked, at];
      all.slice(from, to + 1).forEach((i) => { if (!i.disabled) i.checked = tick.checked; });
    }
    lastPicked = at;
  });
  chaptersBox.addEventListener("change", countChosen);
  foundBox.querySelector(".mh-pick-tools").addEventListener("click", (event) => {
    const how = event.target.closest("[data-pick]");
    if (!how) return;
    tiles().forEach((t) => {
      const input = t.querySelector("input");
      if (!input.disabled) input.checked = how.dataset.pick === "all";
    });
    countChosen();
  });

  // ── «Найти»: the form — a link (its chapters), or (v5.44.75) a name (the titles as covers); a link pasted into the
  // empty field is looked for at once ──
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = field.value.trim();
    if (!text) { say(findButton, "Напишите название тайтла или вставьте ссылку на него", true); field.focus(); return; }
    if (isLink(text)) {
      dropHits();
      find(text, "");
    } else {
      searchName(text);
    }
  });
  field.addEventListener("paste", (event) => {
    const text = (event.clipboardData && event.clipboardData.getData("text") || "").trim();
    const whole = !field.value.trim() || (field.selectionStart === 0 && field.selectionEnd === field.value.length);
    if (!whole || !/^https?:\/\/\S+$/i.test(text)) return;
    event.preventDefault();
    field.value = text;
    if (!findButton.disabled) form.requestSubmit();
  });

  // ── «Скачать», «Пауза», «Остановить» ──
  actions["manhwa-get-go"] = async (button) => {
    if (!found) return;
    const ids = chosenIds();
    const where = foundBox.querySelector('input[name="mh-where"]:checked');
    const follow = foundBox.querySelector('input[name="follow"]');
    try {
      const reply = await postJson("/api/manhwa/download", { find: findId, where: where ? where.value : "",
        follow: follow ? follow.checked : true, helpers: helpersOn(), chapters: ids });
      if (reply && reply.queued) {               // another download goes: this one waits for its turn
        say(button, (reply.queued > 1 ? "В очереди " + reply.queued + "-м" : "В очереди следующим")
          + ": начнётся само, когда закончится текущее скачивание");
        queueHere = { id: reply.queue_id, find: findId, chosen: ids };
        paintRun();
        getJob.hidden = false;
        watchGet().poll();
        return;
      }
      say(button, "");
      runItems = new Map();
      ids.forEach((id) => runItems.set(String(id), { id, state: "wait" }));
      runMine = true;
      foundBox.classList.add("downloading");
      getJob.hidden = false;
      getJob.dataset.status = "starting";
      openBox.hidden = true;
      resultBox.hidden = true;
      paintRun();
      watchGet().started();
    } catch (err) {
      say(button, err.message, true);
    }
  };

  actions["manhwa-get-pause"] = (button) => postJson("/api/manhwa/download/pause",
    { pause: button.textContent !== "Продолжить" })
    .then((reply) => {
      button.textContent = reply.paused ? "Продолжить" : "Пауза";
      say(button, reply.paused ? "Ставлю на паузу…" : "");
      if (getWatch) getWatch.poll();
    }).catch((err) => say(button, err.message, true));

  actions["manhwa-get-stop"] = (button) => postJson("/api/manhwa/download/stop", {})
    .then(() => say(button, "Останавливаю…")).catch((err) => say(button, err.message, true));

  // a search or a download that goes on (or just ended) when the page opens; a link from the title's page
  // («Докачать сейчас») — searched at once, unless a search goes; (v5.44.75) a name given (?q=) — searched at once;
  // else the covers of the last search by a name, folded into one line (the title's card under it, if any)
  fetch("/api/manhwa/find-state", { credentials: "same-origin" }).then((r) => r.json())
    .then((st) => {
      if (st.id) { findJob.hidden = false; watchFind(); }
      if (box.dataset.autofind === "1" && field.value.trim() && !RUNNING.includes(st.status || "")) {
        delete box.dataset.autofind;
        form.requestSubmit();
      }
    }).catch(() => {});
  if (box.dataset.autoname) {
    searchName(box.dataset.autoname);
  } else if (box.dataset.autofind !== "1") {
    fetch("/api/manhwa/search-state?id=last", { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => {
        if (!st.id || hitsId || !(st.titles || []).length || (st.age || 0) > 6 * 3600) return;
        startHits(st.id, st.query || "");
        showHits(st);
        hitsBox.classList.add("folded");
        showFold();
        if (!st.done) pollHits();
      }).catch(() => {});
  }
  fetch("/api/manhwa/download-state", { credentials: "same-origin" }).then((r) => r.json())
    .then((st) => { if (st.id || st.restoring || (st.queue || []).length) watchGet(); }).catch(() => {});
})();
