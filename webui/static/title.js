// Local Booru — the page of a title (v5.44.102, webui/pages/title.py; the owner's approved mockup «Тайтл из «Онлайн»»).
// The title is found by the machinery of «Скачать» (/api/manhwa/find → find-state; its cover — find-cover) and shown
// here: names, facts, the chapters' language or translators, «Продолжить» / «Читать с начала», the chapters as rows,
// the newest first («▶ Читать», ticked — «Скачать отмеченные»), their state in the folder and while this title
// downloads (/api/manhwa/download-state).  A gallery of nhentai — its facts, tags and pages (/api/manga/gallery),
// «Скачать» (/api/manga/download).  (v5.44.105) The phone's page (webui/pages/title.py, phone=True) — the same script:
// its reader and its card are its own (data-reader, data-get), its way back is the bar's (data-back of phone.js's
// frame); phone.js gives it LocalBooru (postJson; say — the phone's note at the bottom).
// (v5.44.119, the approved mockup A) One page of a title for every way in («Лента», «Скачать → По ссылке», «Докачать
// сейчас» of the libraries, «К тайтлу» of the reader): «←» goes where it was opened from («← Лента», «← Скачать», «← Вся
// манга», «← К главе 14»); under the buttons — the block «Скачивание» (where, the new chapters at night, the other
// sites, the paid ones in words); the page knows what is on the disk (/api/title/shelf: «в папке», «прочитана», «новая»,
// another site's, «Вы остановились здесь»), and «Продолжить» / «Читать» open the one reader — a chapter of the folder
// from the disk (/manga/r/…, /manhwa/r/…), the others from the site without downloading (/online).
(() => {
  "use strict";
  const LB = window.LocalBooru;
  const box = document.getElementById("title-page");
  if (!LB || !box) return;
  const { postJson, say } = LB;
  const url = box.dataset.url;
  const rating = parseFloat(box.dataset.rating || "0") || 0;
  const phone = !!box.dataset.get;            // the phone's page: its reader, its card, its words
  const $ = (selector) => box.querySelector(selector);
  const CHAPTERS = ["глава", "главы", "глав"];
  const PAGES = ["страница", "страницы", "страниц"];
  const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
    "ноября", "декабря"];
  // (v5.44.116) where the downloads are seen; (v5.44.120) on the phone too «Скачать → Загрузки» (its own section)
  const DOWNLOADS = "«Скачать → Загрузки»";
  const RUNNING = ["starting", "running", "stopping"];
  const ICON = {
    play: ["M8 5.5v13l10.5-6.5z"],
    down: ["M12 4v11m-5-5 5 5 5-5M5 20h14"],
    book: ["M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z", "M4 21V5", "M9 8h6"],
    folder: ["M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 "
      + "17.5z"],
    moon: ["M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"],
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svg(paths, size) {
    const ns = "http://www.w3.org/2000/svg";
    const icon = document.createElementNS(ns, "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("width", String(size || 16));
    icon.setAttribute("height", String(size || 16));
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("class", "tt-icon");
    (Array.isArray(paths) ? paths : [paths]).forEach((d) => {
      const line = document.createElementNS(ns, "path");
      line.setAttribute("d", d);
      icon.append(line);
    });
    return icon;
  }

  function word(n, forms) {
    const d = n % 10, h = n % 100;
    return d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2];
  }
  const plural = (n, forms) => n + " " + word(n, forms);
  // (v5.44.116) the translators' votes as the program writes them: «1,1 млн оценок»
  function votesText(n) {
    if (n >= 1e6) return String(Math.round(n / 1e5) / 10).replace(".", ",") + " млн оценок";
    if (n >= 1e4) return Math.round(n / 1e3) + " тыс. оценок";
    return plural(n, ["оценка", "оценки", "оценок"]);
  }
  function size(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024 * 1024) return Math.max(1, Math.round(n / 1024)) + " КБ";
    return (n / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ";
  }
  function clock(seconds) {
    const m = Math.max(1, Math.round((Number(seconds) || 0) / 60));
    return m < 60 ? m + " мин" : Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "");
  }

  // «02.12.2026» (the sites' dates as the program gives them) → «2 декабря» (with the year when it is not this one)
  function dayWords(text) {
    const m = /^(\d\d?)\.(\d\d?)\.(\d{4})/.exec(String(text || ""));
    if (!m || +m[2] < 1 || +m[2] > 12) return String(text || "");
    return +m[1] + " " + MONTHS[+m[2] - 1] + (+m[3] === new Date().getFullYear() ? "" : " " + m[3]);
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

  // ── «←»: where the page was opened from ──────────────────────────────────────────────────────────────────────
  // «← Лента»: the feed before — back in the history (its place, its «Показать ещё»), else the link.  The page
  // before is asked of the browser's history (the Navigation API): the program sent «Referrer-Policy: no-referrer»
  // until 5.44.116, so the entries of before hide their addresses — the feed marks its own entry (catalog.js: state
  // «lbFeed»); (v5.44.107) the feed anywhere before in the history: back to it at once; none — its link, to the feed's
  // last address, and the feed takes its list back all the same (catalog.js: «lb-feed-return»)
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

  // (v5.44.119) the program's page right before this one (its address), null — not known: the history's entry before
  // (the Navigation API; «Referrer-Policy: same-origin» — the program's addresses are told to itself), without it — the
  // page that opened this one
  function pageBefore() {
    try {
      const nav = window.navigation;
      if (nav && nav.currentEntry && typeof nav.entries === "function") {
        const i = nav.currentEntry.index - 1;
        const entry = i >= 0 ? nav.entries()[i] : null;
        if (!entry || !entry.url) return null;
        const before = new URL(entry.url);
        return before.origin === location.origin ? before : null;
      }
    } catch (_) { /* the page that opened this one */ }
    try {
      const before = new URL(document.referrer);
      return before.origin === location.origin ? before : null;
    } catch (_) {
      return null;
    }
  }

  // (v5.44.119) the ways in this page goes back to by name: the reader («← К главе 14»), «Скачать» («← Скачать»), a
  // title of the library — its «Докачать сейчас» («← Вся манга», «← Вся манхва»); "" — none of them
  function cameFrom() {
    const before = pageBefore();
    if (!before) return null;
    const path = before.pathname.replace(/^\/m(?=\/)/, "");
    if (/^\/(?:manga|manhwa)\/r\/[0-9a-f]{12}/.test(path) || path === "/online") return { way: "reader", before };
    // «Скачать»: «По ссылке», its parts («Манга», «Манхва», «Из архива», «Галерея (booru)»)
    if (path === "/get" || path.startsWith("/get/") || path === "/grab" || /^\/(?:manga|manhwa)\/get$/.test(path)) {
      return { way: "get", before };
    }
    if (/^\/manga\/[gs]\/[0-9a-f]{12}/.test(path)) return { way: "manga", before };
    if (/^\/manhwa\/t\/[0-9a-f]{12}/.test(path)) return { way: "manhwa", before };
    return null;
  }

  // «← К главе 14»: the chapter of the reader before — by its place in the list (/online?…&ch=n) or by the folder's
  // chapter (its address in what the folder has)
  function readerWords(before) {
    let row = null;
    const chapter = /\/(?:manga|manhwa)\/r\/([0-9a-f]{12})/.exec(before.pathname);
    if (chapter) {
      row = [...rows.values()].find((r) => (shelfRow(r.chapter.id).disk || "").endsWith("/" + chapter[1])) || null;
    } else {
      const n = parseInt(before.searchParams.get("ch") || "0", 10);
      row = [...rows.values()].find((r) => r.n === n) || null;
    }
    const number = row && row.chapter.n;
    return number ? "← К главе " + number : "← К чтению";
  }

  const programBefore = () => !!(LB.programBefore && LB.programBefore());

  function paintBack() {
    const from = cameFrom();
    let words = "← Лента", tip = "";
    if (from && from.way === "reader") { words = readerWords(from.before); tip = "Назад — в читалку"; }
    else if (from && from.way === "get") { words = "← Скачать"; tip = "Назад — в «Скачать»"; }
    else if (from && from.way === "manga") { words = "← Вся манга"; tip = "Назад — к тайтлу в «Вся манга»"; }
    else if (from && from.way === "manhwa") { words = "← Вся манхва"; tip = "Назад — к тайтлу в «Вся манхва»"; }
    else if (!feedEntry() && programBefore()) {   // (v5.44.116) another page of the program: «← Назад»
      words = "← Назад";
      tip = "На прошлую страницу программы";
    }
    document.querySelectorAll("[data-tt-back]").forEach((back) => {
      back.textContent = words;
      back.title = tip;
    });
  }

  document.querySelectorAll("[data-tt-back], .m-top [data-back]").forEach((back) => {
    back.addEventListener("click", (event) => {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (cameFrom()) {                           // the reader, «Скачать», the library: right before this page
        history.back();
        return;
      }
      const entry = feedEntry();
      if (!entry) {
        if (programBefore()) history.back();         // (v5.44.116) «← Назад»
        else toFeed(back);
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

  // (v5.44.129; the owner: «при открытии страницы с интернета (в ленте) теги и все плитки связанные с каким либо
  // поиском не кликабельны») a genre or tag — a search: «Лента» of this tab with it chosen (the server says which —
  // ``title.chips_of``: g, tg), a gallery's tag — nhentai's galleries of it in «Лента» (``href``); a name the program
  // makes no search of (``never``) — no search, words only
  const FEED = (phone ? "/m/" : "/") + box.dataset.kind + "/feed";
  // (v5.44.148; the owner: «с некоторых сайтов теги кликабельны но отправляют в библиотеку а не в ленте смотреть») a
  // title of a site — always «Лента»: its filter when the feed has one of that name (or of another name of it), else
  // the feed's search by the word (until then a word the feed had no filter for went to the tab's folder)
  // (v5.44.149) an author — the feed's list of their titles: the person as MangaLib numbers them (``sg``), or the
  // feed's «Найти тег» filled with the name — it picks the person of exactly that name itself (``find``)
  function chipHref(c) {
    if (c.href) return c.href;
    if (c.g) return FEED + "?g=" + encodeURIComponent(c.g);
    if (c.tg) return FEED + "?tg=" + encodeURIComponent(c.tg);
    if (c.sg) return FEED + "?sg=" + encodeURIComponent(c.sg);
    if (c.never) return "";
    if (c.find) return FEED + "?find=" + encodeURIComponent(c.find) + "&pick=lib";
    return FEED + "?q=" + encodeURIComponent(c.name.trim());
  }

  function chips(items, cls) {
    const list = el("ul", "tt-chips" + (cls ? " " + cls : ""));
    items.forEach((item) => {
      const c = typeof item === "string" ? { name: item } : item;
      const li = el("li", c.tag ? "tt-chip-tag" : "");
      const href = chipHref(c);
      if (href) {
        const a = el("a", "", c.name);
        a.href = href;
        a.title = c.sg || c.find ? "Его тайтлы в «Ленте»"
          : c.href || c.g || c.tg ? "Найти в «Ленте»" : "Искать в «Ленте» по слову";
        li.classList.add("tt-chip-go");
        li.append(a);
      } else {
        li.textContent = c.name;
      }
      list.append(li);
    });
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
  // (v5.44.117) this page's own search (another page's «Найти» does not take its place): its number, and the mark of
  // what is shown of it — the link's site's chapters come at once, the other sites' as they answer
  let findId = 0;
  let findMark = "";
  let coverOf = 0;           // the search whose cover is shown
  let keepingUp = 0;         // the search whose later answers are followed
  let shown = null;          // the title shown: its find's answer
  let rows = new Map();      // a chapter's id → its row
  let order = "new";         // the newest first, as the mockup
  let watching = false;      // the downloads followed (one loop at a time)
  let running = null;        // a download going: {mine} — this title's or another's
  let waitFor = 0;           // a download asked here: until when it may still be about to show (ms)
  const missed = new Map();  // a chapter that did not come in a download of this page: its words
  let followTick = null;     // (v5.44.116) «Новые главы — скачивать самой» of the title shown
  let helpersTick = null;    // (v5.44.119) «брать с других сайтов» of the title shown
  let whereNow = "";         // (v5.44.119) «Куда»: "manga" — by pages, "manhwa" — a strip
  let shelfData = null;      // (v5.44.119) what the title's folder has (/api/title/shelf)
  let shelfAsked = "";       // …asked for this mark of the search
  let shelfAgain = false;    // …asked again while a question goes

  async function find(branch, again) {
    wait("Читаю тайтл с сайта " + box.dataset.site + "…");
    const retry = () => find(branch, true).then((st) => { if (st) showTitle(st); });
    const until = Date.now() + 180000;
    let id = 0;
    for (;;) {
      try {
        id = (await postJson("/api/manhwa/find", { url, branch: branch || "", again: !!again,
          name: box.dataset.name || "" })).id;              // (v5.44.112) the tile's name: the link's site 404 — by it
        break;
      } catch (err) {
        // (v5.44.117) several titles are searched already (tabs opened together): this one begins after one of them
        if (/^Сейчас ищутся уже/.test(err.message || "") && Date.now() < until) {
          wait("Сейчас ищутся другие тайтлы — этот начнётся, как только один из них найдётся…");
          await sleep(2000);
          continue;
        }
        fail(err.message, retry);
        return null;
      }
    }
    let after = 0;
    while (Date.now() < until) {
      let st = null;
      try {
        st = await getJson("/api/manhwa/find-state?find=" + encodeURIComponent(id) + "&after=" + after);
      } catch (err) {
        await sleep(1000);
        continue;
      }
      if (st.gone) {                             // (v5.44.117) this page's search is not there any more
        fail("Поиск этого тайтла потерялся.", retry);
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

  // ── what the folder has (v5.44.119): its chapters, read ones, where it was left ──────────────────────────────────
  const shelfRow = (id) => (shelfData && shelfData.rows && shelfData.rows[String(id)]) || {};
  const helpersOn = () => (helpersTick ? helpersTick.checked : !!(shown && shown.helpers_on !== false));
  const rowHave = (r) => !!(r.chapter.have || shelfRow(r.chapter.id).have);
  const rowRead = (r) => !!shelfRow(r.chapter.id).read;
  // another site's chapter while «брать с других сайтов» is off: not downloaded now (read from the site — still)
  const rowOff = (r) => !!(r.chapter.from && !helpersOn() && !rowHave(r));
  const readable = (r) => rowHave(r) || !r.chapter.wait;
  // a chapter of the folder — the reader of the folder (from the disk), the others — the site's, without downloading
  const readHref = (r, extra) => {
    const disk = shelfRow(r.chapter.id).disk;
    return disk ? disk + (extra ? "?" + extra : "") : onlineHref(shown, r.n, extra ? "&" + extra : "");
  };

  async function askShelf() {
    if (!findId || !shown) return;
    const mark = findMark;
    if (shelfAsked === mark) { shelfAgain = true; return; }
    shelfAsked = mark;
    try {
      const reply = await getJson("/api/title/shelf?find=" + encodeURIComponent(findId) + (phone ? "&m=1" : ""));
      if (findMark !== mark) return;
      shelfData = reply;
      rows.forEach(paintRow);
      paintCounts();
      paintActions();
      paintBack();
    } catch (_) {
      /* the page as the site has it: the folder's marks come with the next answer */
    } finally {
      if (shelfAsked === mark) shelfAsked = "";
      if (shelfAgain) {
        shelfAgain = false;
        askShelf();
      }
    }
  }

  function showTitle(st) {
    const f = st.found;
    // (v5.44.117) the same search drawn again (another site answered): the chapters ticked stay ticked
    const again = st.id === findId && !!shown;
    const tickedBefore = again ? new Set(ticked().map(String)) : null;
    const before = again ? new Set([...rows].filter(([, r]) => !r.tick.disabled).map(([id]) => id)) : null;
    const choices = again ? { where: whereNow, helpers: helpersOn(), follow: followTick && followTick.checked } : null;
    if (!again) shelfData = null;
    findId = st.id;
    box.dataset.find = String(st.id);           // (v5.44.123) the menu of covers (covers.js) asks by this search
    findMark = st.mark || String(st.id);
    shown = f;
    if (hiddenByMode(!!f.hentai)) return;
    $(".tt-hero").removeAttribute("aria-busy");
    wait("");
    if (f.cover && coverOf !== findId) {
      coverOf = findId;
      cover("/api/manhwa/find-cover?find=" + encodeURIComponent(findId));
    } else if (f.cover_hidden) $("[data-tt-cover]").textContent = "";
    $("[data-tt-facts]").textContent = [f.type, f.year, f.status, f.age].filter(Boolean).join(" · ");
    $("[data-tt-name]").textContent = f.title;
    document.title = f.title + " — Local Booru";
    const alt = $("[data-tt-alt]");
    alt.textContent = (f.alt || []).join(" · ");
    alt.hidden = !alt.textContent;

    const holder = $("[data-tt-about]");
    holder.querySelectorAll(".tt-extra").forEach((node) => node.remove());
    const extra = el("div", "tt-extra");
    const branches = f.branches || [];
    if (branches.length > 1) extra.append(branchPicker(f, branches));
    (f.notes || []).forEach((note) => extra.append(el("p", "tt-note warn", note)));
    // (v5.44.117) the other sites, asked at once: their chapters and the description come as they answer
    if ((f.asking || []).length) extra.append(el("p", "tt-note tt-asking", "Ищу на других сайтах: " + f.asking.join(", ") + "…"));
    // (v5.44.146) the sites that did not answer in time: said (it was only in the search's journal), and what follows
    else if ((f.late || []).length) {
      extra.append(el("p", "tt-note tt-late", "Не успели ответить: " + f.late.join(", ")
        + ". Если какой-то главы не окажется на остальных сайтах, при скачивании программа спросит "
        + (f.late.length > 1 ? "их" : "его") + " ещё раз."));
    }
    extra.append(el("div", "tt-actions"));
    extra.append(getBlock(f, choices));          // (v5.44.119) «Скачивание» — the card of «По ссылке» of before
    if (f.about) extra.append(about(f.about));
    // (v5.44.112) the description and the facts — of the owner's first site that has them (Remanga first)
    if (f.about && f.about_site && f.about_site !== f.site_label) {
      extra.append(el("p", "tt-desc-from", `Описание — с ${f.about_site}, главы — с ${f.site_label}`));
    }
    if ((f.people || []).length) {                // (v5.44.149) the authors, each the feed's list of their titles
      const line = el("div", "tt-tags tt-people");
      line.append(el("span", "tt-label", f.people.length > 1 ? "Авторы" : "Автор"));
      line.append(chips(f.people));
      extra.append(line);
    }
    const tagged = f.chips || f.genres || [];     // (v5.44.129) the genres and tags, each a search
    if (tagged.length) extra.append(chips(tagged));
    holder.append(extra);
    showList(f);
    if (tickedBefore) {
      rows.forEach((r, id) => { if (!r.tick.disabled && before.has(id)) r.tick.checked = tickedBefore.has(id); });
    }
    paintCounts();
    paintActions();
    paintBack();                                 // «← К главе N»: the reader's chapter among the rows now
    follow();
    askShelf();
    if (st.partial) keepUp(st.id);
  }

  // (v5.44.117) the other sites still answer: the title drawn again each time one did, until the search ends
  async function keepUp(id) {
    if (keepingUp === id) return;
    keepingUp = id;
    let troubles = 0;
    while (keepingUp === id && findId === id) {
      await sleep(700);
      let st;
      try {
        st = await getJson("/api/manhwa/find-state?find=" + encodeURIComponent(id) + "&after=999999999&seen="
          + encodeURIComponent(findMark));
        troubles = 0;
      } catch (_) {
        if (++troubles > 20) break;
        continue;
      }
      if (findId !== id || st.gone) break;
      if (st.found && !st.found_same) showTitle(st);
      if (!RUNNING.includes(st.status)) break;
    }
    if (keepingUp === id) keepingUp = 0;
  }

  // the numbers under the name: ★, the chapters, the newest one's date, the site, (v5.44.119) «в папке: N»,
  // «прочитано: N»
  function showNumbers(f) {
    const numbers = $("[data-tt-numbers]");
    numbers.textContent = "";
    const list = f.list || [];
    if (rating) numbers.append(el("span", "tt-rate", "★ " + rating.toFixed(1).replace(".", ",")));
    // the chapters of the list (with the other sites'), as «Главы N» below; the date — of the site's own newest one
    numbers.append(el("span", "", plural(list.length || f.chapters, CHAPTERS)));
    if (f.updated && !(list.length && list[list.length - 1].from)) {
      numbers.append(el("span", "", "последняя — " + dayWords(f.updated)));
    }
    numbers.append(el("span", "", "с " + f.site_label));
    const all = [...rows.values()];
    const here = all.filter(rowHave).length || (rows.size ? 0 : f.have);
    if (here) numbers.append(el("span", "tt-have", "в папке: " + here));
    const read = all.filter(rowRead).length;
    if (read) numbers.append(el("span", "tt-done", "прочитано: " + read));
  }

  function branchPicker(f, branches) {
    const wrap = el("div", "tt-branches");
    const label = f.languages ? "Язык глав" : "Переводчики";
    wrap.append(el("span", "tt-label", label));
    const group = el("div", "tt-pick");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", label);
    branches.forEach((b) => {
      const words = (b.name || b.id || "без названия") + " · " + b.count + " гл.";
      const pick = button("tt-chip-btn", words);
      pick.setAttribute("aria-pressed", String(b.id) === String(f.branch) ? "true" : "false");
      if (b.votes > 0) {                         // (v5.44.119) the votes the card of «По ссылке» showed
        pick.title = votesText(b.votes);
        pick.setAttribute("aria-label", words + ", " + votesText(b.votes));
      }
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

  // «Продолжить: Том 2 Глава 14, стр. 12» (where it was left — the freshest of the folder's memory and of the reading
  // without downloading; read to its end — the next one), «Читать с начала», «Открыть в библиотеке»
  function paintActions() {
    const actions = $(".tt-actions");
    if (!actions || !shown) return;
    actions.textContent = "";
    const list = [...rows.values()];
    const first = list.find(readable);
    const last = shelfData && shelfData.last;
    let go = null;
    if (last) {
      const at = last.id ? list.findIndex((r) => String(r.chapter.id) === String(last.id)) : -1;
      if (last.done) {
        const next = at >= 0 ? list.slice(at + 1).find(readable) : null;
        if (next) go = { words: "Продолжить: " + next.chapter.label, href: readHref(next, "p=1") };
      } else if (last.href) {
        go = { words: "Продолжить: " + last.label + (last.page > 0 ? ", стр. " + (last.page + 1) : ""), href: last.href };
      }
    }
    if (go) actions.append(link("tt-btn primary tt-go", go.words, go.href, ICON.play));
    if (first) {
      actions.append(link(go ? "tt-btn tt-start" : "tt-btn primary tt-start", "Читать с начала", readHref(first, "p=1"),
        go ? null : ICON.play));
    }
    const library = shelfData && shelfData.folder && shelfData.folder.library;
    if (library) actions.append(link("tt-btn quiet tt-library", phone ? "В библиотеке" : "Открыть в библиотеке", library,
      ICON.book));
    // (v5.44.122) the phone: «Это хентай» / «Это не хентай» of the title in its library — the server's own row
    // (phone.mark_box: the computer's API, phone.js answers its button)
    const before = actions.parentNode.querySelector(":scope > .m-mark");
    if (before) before.remove();
    if (phone && shelfData && shelfData.mark) actions.insertAdjacentHTML("afterend", shelfData.mark);
  }

  // ── «Скачивание» (v5.44.119): what the card of «По ссылке» had — where, the new chapters at night, the other sites
  // and the chapters they give, the paid ones in words with their dates; «Докачать — N глав» ──
  function getBlock(f, choices) {
    const block = el("section", "tt-get");
    block.setAttribute("aria-labelledby", "tt-get-h");
    const head = el("div", "tt-get-head");
    const titleBox = el("div", "tt-get-title");
    const h2 = el("h2", "tt-get-h");
    h2.id = "tt-get-h";
    h2.append(svg(ICON.down, 15), el("span", "", "Скачивание"));
    titleBox.append(h2, el("span", "tt-get-note"));
    const all = button("tt-btn tt-get-go", "", ICON.down);
    all.dataset.ttAll = "1";
    all.addEventListener("click", () => download(null, all));
    head.append(titleBox, all);
    const rowsBox = el("dl", "tt-get-rows");
    const row = (name, key) => {
      const item = el("div", "tt-get-row");
      item.dataset.row = key;
      const dd = el("dd");
      item.append(el("dt", "", name), dd);
      rowsBox.append(item);
      return dd;
    };
    // «Куда»: the manga — page by page, the strips — the manhwa (the folder of manga not chosen: the manhwa's)
    const rootsOk = !!(f.roots && f.roots.manga);
    whereNow = choices && choices.where ? choices.where : f.kind === "manga" && rootsOk ? "manga" : "manhwa";
    const where = row("Куда", "where");
    const seg = el("div", "tt-seg");
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", "Куда");
    [["manga", "В мангу — по страницам"], ["manhwa", "В манхву — лентой"]].forEach(([value, words]) => {
      const pick = el("button", "", words);
      pick.type = "button";
      pick.dataset.value = value;
      pick.setAttribute("aria-pressed", value === whereNow ? "true" : "false");
      if (value === "manga" && !rootsOk) {
        pick.disabled = true;
        pick.title = "Папка манги не выбрана — «Настройки → Хранение и копии»";
      }
      pick.addEventListener("click", () => {
        if (whereNow === value) return;
        whereNow = value;
        seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === pick ? "true" : "false"));
        paintCounts();
      });
      seg.append(pick);
    });
    const whereSum = el("p", "tt-get-sum tt-get-folder");
    whereSum.append(svg(ICON.folder, 14), el("span"));
    where.append(seg, whereSum);
    // «Новые главы — скачивать самой» (v5.44.116: the title's own choice — source.json «follow»; a title never
    // downloaded — on) and the nightly round's last look at it
    const night = row("Новые главы", "night");
    const keep = el("label", "tt-check tt-follow");
    followTick = el("input");
    followTick.type = "checkbox";
    followTick.checked = choices && choices.follow !== undefined && choices.follow !== null ? !!choices.follow
      : f.follow !== false;
    keep.append(followTick, el("span", "", "Скачивать самой — раз в сутки, ночью"));
    followTick.addEventListener("change", () => save({ follow: followTick.checked }));
    const nightSum = el("p", "tt-get-sum tt-get-night");
    nightSum.append(svg(ICON.moon, 14), el("span"));
    nightSum.hidden = true;
    night.append(keep, nightSum);
    // «Другие сайты»: the chapters the link's site has not free — from the others (and how many each gives)
    helpersTick = null;
    const giving = (f.helpers || []).filter((h) => h.gives > 0);
    if (giving.length) {
      const others = row("Другие сайты", "others");
      const box2 = el("label", "tt-check tt-helpers");
      helpersTick = el("input");
      helpersTick.type = "checkbox";
      helpersTick.checked = choices ? choices.helpers : f.helpers_on !== false;
      box2.append(helpersTick, el("span", "", "Главы, которых нет бесплатно на " + f.site_label
        + ", — брать с других сайтов: " + giving.map((h) => h.site_label + " (" + plural(h.gives, CHAPTERS) + ")")
          .join(", ")));
      // each site's title there and its chapters (the card of «По ссылке» listed them)
      box2.title = giving.map((h) => h.site_label + " — «" + h.title + "»: " + plural(h.gives, CHAPTERS)
        + (h.first ? " (" + h.first + (h.last !== h.first ? " — " + h.last : "") + ")" : "")).join("\n");
      helpersTick.addEventListener("change", () => {
        rows.forEach(paintRow);
        paintCounts();
        paintActions();
        save({ helpers: helpersTick.checked });
      });
      others.append(box2);
    } else if ((f.helpers || []).length) {
      row("Другие сайты", "others").append(el("p", "tt-get-sum", "Тайтл есть и на "
        + f.helpers.map((h) => h.site_label).join(", ") + " — новых глав оттуда сейчас нет."));
    }
    // «Платные» — in words, with the days they become free (the program never buys them)
    const paid = row("Платные", "paid");
    paid.append(el("p", "tt-get-paid"));
    const status = el("p", "tt-status action-status");
    status.setAttribute("role", "status");
    block.append(head, rowsBox, status);
    return block;
  }

  // the boxes of a title downloaded (its source.json): kept at once; one never downloaded — with its «Скачать»
  async function save(change) {
    if (!shelfData || !shelfData.saved) return;
    const status = $(".tt-status");
    try {
      await postJson("/api/manhwa/follow", { find: findId, ...change });
      say(status, "follow" in change ? (change.follow ? "Новые главы этого тайтла программа скачает сама — ночью"
        : "Новые главы этого тайтла программа сама не скачивает") : (change.helpers
        ? "Главы с других сайтов — берутся" : "Главы с других сайтов — не берутся"));
    } catch (err) {
      say(status, err.message, true);
    }
  }

  // «На MangaLib платных 9: 5 из них есть бесплатно на других сайтах. Остальные 4 программа не покупает — возьмёт
  // сама, когда MangaLib сделает их бесплатными: Глава 125 — 12 ноября, …»; one — «На MangaLib одна платная глава —
  // программа её не покупает и возьмёт сама, когда MangaLib сделает её бесплатной: …» (not «платных 1 — … их»)
  function paidWords(f) {
    const on = helpersOn();
    const count = f.paid_count || 0;
    if (!count) return "";
    const left = on ? f.paid_left : count;
    const covered = count - left;
    const items = on ? (f.waiting || []) : (f.paid || []);
    const dated = items.filter((c) => c.free_at);
    const days = dated.slice(0, 5).map((c) => c.label + " — " + dayWords(c.free_at)).join(", ")
      + (dated.length > 5 || left > 5 ? " и другие" : "");
    const site = f.site_label;
    const one = left === 1;                      // the words of one chapter left: «её», «бесплатной», «появится»
    const nowhere = one ? "бесплатно её пока нет ни на одном из сайтов: появится — программа скачает сама."
      : "бесплатно их пока нет ни на одном из сайтов: появятся — программа скачает сама.";
    const takes = "возьмёт сама, когда " + site + " сделает " + (one ? "её бесплатной: " : "их бесплатными: ") + days + ".";
    const head = count === 1 ? "На " + site + " одна платная глава" : "На " + site + " платных " + count;
    if (covered && !left) {
      return head + (count === 1 ? " — она есть" : " — все есть") + " бесплатно на других сайтах.";
    }
    if (covered) {
      return head + ": " + covered + " из них есть бесплатно на других сайтах. "
        + (one ? "Ещё одну" : "Остальные " + left) + " программа не покупает — " + (dated.length ? takes : nowhere);
    }
    return head + " — программа " + (one ? "её" : "их") + " не покупает"
      + (dated.length ? " и " + takes : "; " + nowhere);
  }

  function chapterTitle(c) {
    return (c.tome ? "Том " + c.tome + " · " : "") + "Глава " + (c.n || "?") + (c.name ? " — " + c.name : "");
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
    const turn = button("tt-link tt-order", order === "new" ? "↓ сначала новые" : "↑ сначала первые");
    turn.addEventListener("click", () => {
      order = order === "new" ? "old" : "new";
      turn.querySelector("span").textContent = order === "new" ? "↓ сначала новые" : "↑ сначала первые";
      place();
    });
    const legend = el("ul", "tt-legend");
    legend.setAttribute("aria-label", "Главы по папке");
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
    head.append(h2, turn, legend, tools);
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
      tick.setAttribute("aria-label", "Отметить: " + chapterTitle(c));
      tick.addEventListener("change", paintCounts);
      const name = el("div", "tt-row-name");
      const sub = el("span", "tt-row-sub");
      sub.hidden = true;
      name.append(el("span", "tt-row-title", chapterTitle(c)), sub);
      const marks = el("span", "tt-marks");
      const state = el("span", "tt-row-state");
      marks.append(state);
      const read = link("tt-btn small quiet tt-read", "Читать", "#", ICON.play);
      const none = el("span", "tt-read-none");
      row.append(tick, name, marks, none);
      ol.append(row);
      const r = { row, tick, sub, marks, state, read, none, chapter: c, n: i + 1, dl: null, held: false,
        queuedMark: false };
      rows.set(String(c.id), r);
      paintRow(r);
    });
    place();
    section.hidden = false;
  }

  // a row as it is now: the folder's marks (v5.44.119: «новая», another site's, «прочитана», «в папке»), its state in a
  // download, the line under its name, «Читать» / «Продолжить» — the folder's reader or the site's
  function paintRow(r) {
    const c = r.chapter;
    const s = shelfRow(c.id);
    const have = rowHave(r);
    const read = rowRead(r);
    const off = rowOff(r);
    const last = shelfData && shelfData.last;
    const here = !!(last && !last.done && last.id && String(last.id) === String(c.id) && readable(r));
    const home = (shown && shown.site_label) || "";
    r.row.classList.toggle("here", here);
    r.row.classList.toggle("read", read && !here);
    r.row.classList.toggle("wait", !have && (!!c.wait || off));
    if (!r.held) {
      const locked = have || !!c.wait || off;
      if (locked) r.tick.checked = false;
      r.tick.disabled = locked;
    }
    r.marks.querySelectorAll(".tt-mark").forEach((x) => x.remove());
    const marks = [];
    if (s.new && !read) marks.push(["new", "новая"]);
    const from = have ? s.from : (c.from && helpersOn() ? c.from : "");
    if (from) marks.push(["from", "с " + from]);
    if (read && !here) marks.push(["read", "✓ прочитана"]);
    marks.forEach(([cls, text]) => r.marks.insertBefore(el("span", "tt-mark " + cls, text), r.state));
    let text = "", cls = "";
    if (r.dl) [text, cls] = [r.dl.text, r.dl.cls];
    else if (r.queuedMark && !have) [text, cls] = ["в очереди", "going"];
    else if (missed.has(String(c.id)) && !have) [text, cls] = [missed.get(String(c.id)), "bad"];
    else if (have) [text, cls] = ["✓ в папке", "have"];
    else if (c.wait) [text, cls] = [c.free_at ? "платная до " + dayWords(c.free_at) : "бесплатно её нет нигде", "wait"];
    else if (off) [text, cls] = [c.paid ? "платная" + (c.free_at ? " до " + dayWords(c.free_at) : "") : "нет на " + home,
      "wait"];
    r.state.textContent = text;
    r.state.className = "tt-row-state" + (cls ? " " + cls : "");
    r.state.hidden = !text;
    r.state.title = (r.dl && r.dl.why) || "";
    let sub = "";
    if (here) sub = "Вы остановились здесь · стр. " + (last.page + 1) + (last.pages ? " из " + last.pages : "");
    else if (have && s.night && s.came) sub = "пришла " + s.came + " — ночная проверка";
    else if (have && s.from) {
      sub = "на " + home + (c.paid ? " платная" : String(c.id).includes(":") ? " её нет" : " тогда была платной");
    } else if (!have && c.from && helpersOn()) {
      sub = "на " + home + (c.paid ? " платная" : " её нет") + " — на " + c.from + " бесплатно";
    }
    r.sub.textContent = sub;
    r.sub.className = "tt-row-sub" + (here ? " here" : "");
    r.sub.hidden = !sub;
    if (!readable(r)) {
      if (r.read.isConnected) r.read.replaceWith(r.none);
      return;
    }
    if (!r.read.isConnected) r.none.replaceWith(r.read);
    r.read.href = here ? last.href : readHref(r);
    r.read.className = "tt-btn small tt-read " + (here ? "primary" : "quiet");
    r.read.querySelector("span").textContent = here ? "Продолжить" : "Читать";
    r.read.setAttribute("aria-label", (here ? "Продолжить: " : "Читать: ") + chapterTitle(c)
      + (have && s.disk ? " — из папки" : " — с сайта, без скачивания"));
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

  // the counts: the numbers under the name, the legend over the chapters, the block «Скачивание», the buttons
  function paintCounts() {
    if (!shown) return;
    const f = shown;
    const list = [...rows.values()];
    const have = list.filter(rowHave).length;
    const read = list.filter(rowRead).length;
    const open = list.filter((r) => !r.tick.disabled).length;            // what a download takes now
    const notHere = list.filter((r) => !rowHave(r) && !r.chapter.wait && !rowOff(r)).length;
    const waiting = list.filter((r) => !rowHave(r) && (r.chapter.wait || (rowOff(r) && r.chapter.paid))).length;
    const paidLeft = waiting > 0;
    // nothing to take now, nothing in the folder yet: the title kept, its paid ones awaited by the nightly round
    const waits = !open && !have && paidLeft;
    const mine = !!(running && running.mine);
    const other = !!(running && !running.mine);     // another title downloads: this one goes into the queue
    const queuedRows = list.some((r) => r.queuedMark && !rowHave(r));   // (v5.44.107) its chapters wait in the queue
    // (v5.44.117) «всё» — once the other sites have said what they have (their chapters are part of it)
    const asking = (f.asking || []).length > 0;
    showNumbers(f);
    const legend = $(".tt-legend");
    if (legend) {
      legend.textContent = "";
      [["have", "в папке", have], ["read", "прочитано", read], ["missing", "не скачаны", notHere],
        ["wait", "ждут бесплатных", waiting]].forEach(([key, words, n]) => {
        if (!n && key !== "have") return;
        const item = el("li", "", words + " — " + n);
        item.dataset.count = key;
        legend.append(item);
      });
    }
    const note = $(".tt-get-note");
    if (note) {
      note.textContent = (notHere ? plural(notHere, CHAPTERS) + " ещё не в папке" : "Все главы, что есть бесплатно, — в папке")
        + (waiting ? " · " + (waiting === 1 ? "1 ждёт, пока станет бесплатной" : waiting + " ждут, пока станут бесплатными")
          : "");
    }
    const all = $("[data-tt-all]");
    if (all) {
      all.querySelector("span").textContent = mine ? "Качается…" : asking ? "Ищу главы на других сайтах…"
        : open ? (other ? "В очередь — " : have ? "Докачать — " : "Скачать всё — ") + plural(open, CHAPTERS)
          : queuedRows ? "В очереди" : waits ? "Ждать платные" : paidLeft ? "Бесплатные — в папке" : "Всё уже в папке";
      all.title = waits || (!open && paidLeft) ? "Платные программа скачает сама, когда они станут бесплатными" : "";
      all.disabled = mine || asking || (!open && !waits);
    }
    const marked = $("[data-tt-marked]");
    if (marked) {
      // (v5.44.107) while this title downloads too: the chapters ticked go into the queue after it
      const n = ticked().length;
      marked.querySelector("span").textContent = (running ? "В очередь отмеченные" : "Скачать отмеченные") + " (" + n + ")";
      marked.disabled = !n;
    }
    // «В папку манги — «Синий меч», там уже 41 глава · Показать папку»
    const folderSum = $(".tt-get-folder span");
    if (folderSum) {
      const here = whereNow === f.kind;
      const name = String((here ? f.folder : f.title) || "").split(/[\\/]/).filter(Boolean).pop() || f.title;
      folderSum.textContent = "В папку " + (whereNow === "manga" ? "манги" : "манхвы") + " — «" + name + "»";
      if (here && have) {
        folderSum.append(", там уже ");
        folderSum.append(el("b", "", plural(have, CHAPTERS)));
      }
      if (other) folderSum.append(" · начнётся после текущего скачивания");
      const reveal = here && shelfData && shelfData.folder && shelfData.folder.there && shelfData.folder.reveal;
      if (reveal) {
        folderSum.append(" · ");
        const show = button("tt-link tt-reveal", "Показать папку");
        show.addEventListener("click", async () => {
          try { await postJson(reveal, {}); } catch (err) { say($(".tt-status"), err.message, true); }
        });
        folderSum.append(show);
      }
    }
    // the nightly round: when it looked last and what it brought (a title downloaded — it looks every night)
    const nightSum = $(".tt-get-night");
    if (nightSum) {
      let words = "";
      if (shelfData && !shelfData.watch) {
        words = "Сама программа сейчас новые главы не проверяет — это включается в «Настройки → Манга и манхва»";
      } else if (shelfData && shelfData.night) {
        words = "Последняя проверка — " + shelfData.night.at + ": " + shelfData.night.got;
      } else if (shelfData && shelfData.downloaded) {
        words = "Ночью программа проверит, не вышли ли новые главы";
      }
      nightSum.querySelector("span").textContent = words;
      nightSum.hidden = !words;
    }
    const paid = $(".tt-get-paid");
    if (paid) {
      const words = paidWords(f);
      paid.textContent = words;
      const line = paid.closest(".tt-get-row");
      if (line) line.hidden = !words;
    }
  }

  async function download(chapters, from) {
    const status = $(".tt-status");
    from.disabled = true;
    waitFor = Date.now() + 15000;                 // a loop following now goes on until the download shows
    // (v5.44.117) the other sites still answer: the program waits for them, then begins
    if ((shown.asking || []).length) say(status, "Жду ответа других сайтов — скачивание начнётся само");
    const body = () => ({ find: findId, where: whereNow || shown.kind, helpers: helpersOn(), chapters,
      follow: followTick ? followTick.checked : shown.follow !== false });       // (v5.44.116) the box, not «true»
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
        : "Скачивание началось — его видно и в " + DOWNLOADS);
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
      // (v5.44.146) the chapter the other sites are asked once more for (the bar of the download says so)
      if (x.state === "retry" && x.seek) seekId = String(x.id);
      else if (seekId === String(x.id)) seekId = "";
      const r = rows.get(String(x.id));
      if (!r) return;
      let text = WORDS[x.state] || "";
      if (x.state === "going" && x.pages) text = "качается " + x.got + "/" + x.pages;
      if (x.state === "retry" && x.again) text = "повтор через " + x.again + " с";
      if (x.state === "retry" && x.seek) text = "ищу на других сайтах";
      if (x.state === "failed" && x.why) text = "не пришла: " + x.why;
      if (x.state === "failed") missed.set(String(x.id), text);
      else missed.delete(String(x.id));
      r.queuedMark = false;
      // (v5.44.119) why it did not come, and which try comes (the card of «По ссылке» said it on its tile)
      const why = (x.state === "failed" || x.state === "retry") && x.why && !x.seek
        ? "Не скачалась: " + x.why + (x.tries ? " (повтор " + x.tries + " из 3)" : "") : "";
      r.dl = { text, cls: x.state === "done" ? "have" : x.state === "failed" ? "bad" : "going", why };
      if (x.state === "done") {
        r.tick.checked = false;
        r.held = false;
        r.chapter.have = true;
      } else if (x.state === "failed") {
        r.held = false;                            // it can be ticked again
      } else if (!r.tick.disabled || r.held) {     // (v5.44.107) in the download: not ticked twice
        r.tick.checked = false;
        r.tick.disabled = true;
        r.held = true;
      }
      paintRow(r);
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
      if (ids.has(id) && !rowHave(r)) {
        if (!r.queuedMark || !r.held) {
          r.queuedMark = true;
          if (!r.tick.disabled) { r.tick.checked = false; r.tick.disabled = true; r.held = true; }
          paintRow(r);
        }
      } else if (r.queuedMark) {                    // it began, or it was taken out of the queue
        r.queuedMark = false;
        if (r.held && !r.dl) r.held = false;
        paintRow(r);
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
        // (v5.44.119) …and its end: the download seen here as this title's (the page opened again while it went)
        const ours = (!!findId && String(st.find) === String(findId)) || (going && same)
          || (!!st.id && String(st.id) === ourTask);
        const mine = going && ours;
        if (mine && st.id) ourTask = String(st.id);
        running = going ? { mine } : null;
        if (meter.run !== run) { since = 0; run = meter.run || 0; }
        since = Math.max(since, meter.rev || 0);
        if (ours) {
          sawOurs = true;
          paintItems(st.items || []);
          if (!going) waitFor = 0;                  // it has ended (perhaps before the first look): nothing to wait for
          // (v5.44.119) how it ended, in words (the card of «По ссылке» said it): «Готово: скачано 2 главы»
          if (!going && st.id && st.id !== endSaid && ["done", "stopped", "failed"].includes(st.status)) {
            endSaid = st.id;
            ended(st);
          }
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

  let seekId = "";             // (v5.44.146) the chapter the download asks the other sites for now
  let endSaid = "";            // the download whose end is said
  let ourTask = "";            // the download seen going as this title's (its task's number)
  function ended(st) {
    const r = st.result || {};
    const got = Number(r.got) || 0;
    const failed = (r.failed || []).length;
    let text = st.status === "failed" ? (st.error || "Скачивание прервалось — причина в «Загрузках»")
      : (st.status === "stopped" || r.stopped ? "Остановлено: " : "Готово: ")
        + (got ? "скачано " + plural(got, CHAPTERS) : "новых глав не скачано");
    if (st.status !== "failed" && failed) text += ", не скачались: " + failed;
    if (st.status !== "failed" && r.gave_up) text += ". " + r.gave_up;
    if (st.status !== "failed" && failed && r.looked) text += ". " + r.looked;   // (v5.44.146) where it looked
    say($(".tt-status"), text, st.status === "failed" || (!!failed && !got) || !!r.gave_up);
  }

  // this title's download has ended: the chapters as the folder has them now (the program reads the folder again)
  async function refresh() {
    let st;
    try {
      st = await getJson("/api/manhwa/find-state?find=" + encodeURIComponent(findId) + "&after=999999999");
    } catch (_) {
      return;
    }
    if (st.gone && shown) {                     // (v5.44.117) let go meanwhile (long ago): found anew
      const again = await find(String(shown.branch || ""));
      if (again) showTitle(again);
      return;
    }
    if (!st.found || st.id !== findId) return;
    findMark = st.mark || findMark;
    shown = st.found;
    // (v5.44.146) the sites that had not answered in time have, meanwhile (the download asked them once more)
    const lateNote = $(".tt-late");
    if (lateNote && !(shown.late || []).length) lateNote.remove();
    showList(shown);
    paintCounts();
    paintActions();
    askShelf();
  }

  // the download of this title: (v5.44.119, the card of «По ссылке» said so) the chapter going — its strips or pages,
  // the counts, «Пауза» / «Продолжить», «Остановить»
  function showRunning(st, meter) {
    const bar = $(".tt-running");
    if (!bar) return;
    bar.hidden = false;
    bar.textContent = "";
    const now = meter.now;
    const parts = whereNow === "manga" ? ["страницы", "страниц", "страниц"] : ["полосы", "полос", "полос"];
    // (v5.44.163) the round of new chapters waits for the person to leave the computer: «Продолжить» lets it go on
    const held = !!st.held && !st.paused;
    let doing = st.paused ? "Пауза"
      : held ? "Ждёт, пока вы отойдёте от компьютера, — чтобы не занимать процессор" : "Качается";
    if (now) {
      doing = (st.paused ? "Пауза — " : "Качается: ") + now.label + (now.from ? " (с " + now.from + ")" : "")
        + (now.pages ? ": " + now.got + " из " + now.pages + " " + word(now.pages, parts) : "");
    }
    if (meter.speed && !st.paused) doing += " · " + size(meter.speed) + "/с";
    if (meter.eta && !st.paused) doing += " · осталось ≈ " + clock(meter.eta);
    if (seekId && !now) doing = "Ищу главу на других сайтах";   // (v5.44.146) the other sites asked once more
    const again = (meter.retrying || 0) - (seekId ? 1 : 0);
    const words = el("span", "tt-running-words", doing + ". Всего: " + (meter.done || 0) + " из " + (meter.total || 0)
      + (again > 0 ? ", ждут повтора: " + again : "") + (meter.failed ? ", не скачались: " + meter.failed : ""));
    const pause = button("tt-link", st.paused || held ? "Продолжить" : "Пауза");
    pause.addEventListener("click", async () => {
      try {
        await postJson("/api/manhwa/download/pause", { pause: !(st.paused || held) });
      } catch (err) { say(bar, err.message, true); }
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
    // (v5.44.116) all its pages in the folder: «✓ В папке» — «Скачать» would download nothing (it said «Скачать
    // заново»); one cut short — «Докачать N»: the pages a download asks for (``have.missing``, as it counts them)
    const missing = !g.have ? -1 : g.have.missing !== undefined ? Number(g.have.missing) || 0
      : Math.max(0, g.pages - g.have.pages);
    const get = missing === 0 ? button("tt-btn primary", "✓ В папке")
      : button("tt-btn primary", missing > 0 ? "Докачать " + plural(missing, ["страницу", "страницы", "страниц"])
        : "Скачать", ICON.down);
    get.disabled = missing === 0;
    get.dataset.ttGet = "";                          // (v5.44.148) «Скачать» by its own mark: «Читать» may be the main one
    const status = el("p", "tt-status action-status");
    status.setAttribute("role", "status");
    get.addEventListener("click", async () => {
      get.disabled = true;
      try {
        const reply = await postJson("/api/manga/download", { text: String(g.id) });
        // (v5.44.107) another gallery downloads: this one waits in the queue after it
        say(status, reply.already ? "Уже в «Загрузках»" : reply.queued ? "В очереди: начнётся, когда скачаются галереи "
          + "перед ней — её видно в " + DOWNLOADS : "Скачивание началось — его видно в " + DOWNLOADS);
        followGallery(g.id, status);
      } catch (err) {
        say(status, err.message, true);
      }
      get.disabled = false;
    });
    // (v5.44.148; the owner: «на nhentai прочтение через программу не работает там просто не кликабельные картинки»)
    // «Читать» — the gallery in the program's reader without downloading it; where it was left — «Продолжить»
    const reader = g.reader ? (phone ? "/m" : "") + g.reader : "";
    if (reader) {
      const left = g.read && !g.read.done && g.read.page > 1 ? g.read : null;
      const read = link("tt-btn" + (missing === 0 ? "" : " primary"),
        left ? "Продолжить: стр. " + left.page + " из " + left.pages : "Читать", reader + (left ? "" : "&p=1"));
      read.dataset.ttRead = "";
      if (missing !== 0) get.classList.remove("primary");     // one main button: reading, when it is not in the folder
      actions.prepend(read);
      if (g.read && (g.read.done || left)) {
        const again = link("tt-btn", "Читать с начала", reader + "&p=1");
        again.dataset.ttReadStart = "";
        actions.append(again);
      }
    }
    actions.append(get);
    if (g.have && g.have.open) actions.append(link("tt-btn", "Открыть в программе", g.have.open));
    extra.append(actions, status);
    if (g.have) {
      extra.append(el("p", "tt-note", "Уже в папке манги: «" + g.have.folder + "»" + (missing === 0
        ? " — все страницы на месте." : " — страниц там " + g.have.pages + " из " + g.pages + ".")));
    }
    (g.tags || []).forEach((group) => {
      const line = el("div", "tt-tags");
      line.append(el("span", "tt-label", group.kind));
      // (v5.44.129) a click — nhentai's galleries of that artist, tag… in «Лента»; (v5.44.148) as its own tag
      // chosen there («sg=nhentai:artist:name» — a chip with ×, more can be added), not words in the search field
      line.append(chips(group.names.slice(0, 40).map((name) => ({ name, href: group.type
        ? FEED + "?sg=" + encodeURIComponent("nhentai:" + group.type + ":" + name.toLowerCase()) : "" }))));
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
      if (reader) {                                  // (v5.44.148) a page — the reader at that page
        const open = el("a");
        open.href = reader + "&p=" + (k + 1);
        open.title = "Читать со страницы " + (k + 1);
        open.append(img, el("span", "", String(k + 1)));
        if (g.read && !g.read.done && g.read.page === k + 1) cell.classList.add("here");
        cell.append(open);
      } else {
        cell.append(img, el("span", "", String(k + 1)));
      }
      grid.append(cell);
    });
    section.append(head, grid);
    section.hidden = false;
  }

  // «←» as the page was opened (here: the reader's words read the rows — declared above); again once the title is shown
  paintBack();
  if (box.dataset.gallery) {
    showGallery(box.dataset.gallery);
  } else {
    find(box.dataset.branch || "").then((st) => { if (st) showTitle(st); });
  }
})();
