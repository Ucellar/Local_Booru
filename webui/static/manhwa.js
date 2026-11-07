// Local Booru «Манхва» (v5.44.59, webui/pages/manhwa.py): a title's tabs, its chapters (the order, the ticks by hand);
// (v5.44.126) the chapters of a series of «Манга» too — its pages of the computer and the phone (manga.py,
// phone_manga.py) take this script.  (v5.44.117) The reader is the program's one reader — static/reader.js.
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
  // action would keep the box from ticking); (v5.44.62) «брать с других сайтов» — the same way; (v5.44.124) the same
  // block on a title of «Вся манга» — its boxes name their library (data-kind="manga")
  document.addEventListener("change", (event) => {
    const box = event.target;
    if (!(box instanceof HTMLInputElement) || !(box.dataset.follow || box.dataset.helpers)) return;
    const status = box.closest(".mh-source") && box.closest(".mh-source").querySelector(".action-status");
    const body = box.dataset.helpers ? { id: box.dataset.helpers, helpers: box.checked } :
      { id: box.dataset.follow, follow: box.checked };
    if (box.dataset.kind) body.kind = box.dataset.kind;
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

  // the tick «прочитана» by hand; (v5.44.126) a chapter of «Манга» (its page of the computer and of the phone) says its
  // library (data-kind="manga"): the manga's memory of reading
  actions["mh-read"] = async (el) => {
    const read = el.getAttribute("aria-pressed") !== "true";
    const body = { chapter: el.dataset.chapter, read };
    if (el.dataset.kind) body.kind = el.dataset.kind;
    try {
      await postJson("/api/manhwa/read", body);
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
})();

// (v5.44.60) «Скачать манхву» (webui/pages/manhwa_get.py), «Скачать → Манга» before its nhentai: the field — a link of
// a title, or (v5.44.75) its name (the sites' searches at once, the titles as covers).  (v5.44.119, step 3A of the
// plan) A link, or a cover picked, opens the title's one page (webui/pages/title.py: ``/title?url=`` — the tab its
// title belongs to): the card of before — its chapters, its download, the queue, «Новые главы» — is that page's now
// (its block «Скачивание»).  Back from it, the covers of the search are here again, the one picked marked.
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const box = document.getElementById("manhwa-get");
  if (!box || !LB.postJson) return;
  const actions = LB.actions || {};
  const postJson = LB.postJson;
  const say = LB.say || (() => {});
  const form = box.querySelector('[data-form="manhwa-find"]');
  const field = form.querySelector('input[name="url"]');
  const findButton = form.querySelector('button[type="submit"]');
  const part = box.dataset.part === "manga" ? "manga" : "manhwa";
  const PICKED = "mh-get-picked";  // sessionStorage: {search, url} — the cover picked (marked when the page is back)

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
  const TITLES = ["тайтл", "тайтла", "тайтлов"];
  const isLink = (text) => /^https?:\/\/\S+$/i.test(text) || /^(?:www\.)?[\w-]+(?:\.[\w-]+)+\/\S*$/i.test(text);

  function remember(value) {
    try {
      if (value) sessionStorage.setItem(PICKED, JSON.stringify(value));
      else sessionStorage.removeItem(PICKED);
    } catch (_) { /* a page without its storage: nothing is marked when it is back */ }
  }
  function remembered() {
    try {
      return JSON.parse(sessionStorage.getItem(PICKED) || "null") || null;
    } catch (_) {
      return null;
    }
  }

  // ── the title's one page: a link of the field, a cover picked (its kind, name and rating — shown at once there) ──
  function openTitle(url, hit) {
    const kind = hit ? (hit.kind === "manga" ? "manga" : "manhwa") : part;
    let address = "/title?url=" + encodeURIComponent(url) + "&k=" + kind;
    if (hit && hit.title) address += "&t=" + encodeURIComponent(hit.title);
    if (hit && hit.rating) address += "&r=" + encodeURIComponent(String(hit.rating));
    findButton.disabled = true;
    say(findButton, "Открываю страницу тайтла…");
    location.assign(address);
  }

  // ── «Найти» by a name: the sites' searches at once, the titles as covers (core/manhwa_search.py) ──
  const hitsBox = box.querySelector(".mh-hits");
  const hitsTitle = hitsBox.querySelector("#mh-hits-title");
  const hitsSites = hitsBox.querySelector(".mh-hits-sites");
  const hitsList = hitsBox.querySelector(".mh-hits-list");
  const hitsFold = hitsBox.querySelector(".mh-hits-fold");
  // the part's own kind first: «Скачать → Манга» — the manga (turned page by page), «Манхва» — the strips
  const GROUPS = part === "manga"
    ? [["manga", "Манга"], ["strip", "Манхва, маньхуа и другие"]]
    : [["strip", "Манхва, маньхуа и другие"], ["manga", "Манга"]];
  let hitsId = 0;                  // the search shown
  let hitsTimer = 0;
  let hitsQuery = "";
  let hitsCount = 0;
  let picked = "";                 // the address of the title picked (its page opened)
  const hitTiles = new Map();      // a title's address → its tile

  function searchName(text) {
    clearTimeout(hitsTimer);
    findButton.disabled = true;
    postJson("/api/manhwa/search", { q: text }).then((reply) => {
      findButton.disabled = false;
      say(findButton, "");
      remember(null);
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
    hitsFold.title = name ? "Последним открыт «" + name + "»" : "";
  }

  function markPicked(url) {
    picked = url;
    hitTiles.forEach((x, key) => {
      x.classList.toggle("picked", key === url);
      x.setAttribute("aria-pressed", key === url ? "true" : "false");
    });
  }

  function pickHit(url) {
    const tile = hitTiles.get(url);
    if (!tile || !tile.hit) return;
    markPicked(url);
    remember({ search: hitsId, url });
    openTitle(url, tile.hit);
  }

  actions["manhwa-hits-all"] = () => {
    hitsBox.classList.remove("folded");
    hitsFold.hidden = true;
    const tile = hitTiles.get(picked);
    (tile || hitsBox).scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (tile) tile.focus({ preventScroll: true });
  };

  // ── «Найти»: the form — a link (its page), or a name (the titles as covers); a link pasted into the empty field is
  // opened at once ──
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = field.value.trim();
    if (!text) { say(findButton, "Напишите название тайтла или вставьте ссылку на него", true); field.focus(); return; }
    if (isLink(text)) {
      dropHits();
      remember(null);
      openTitle(/^https?:\/\//i.test(text) ? text : "https://" + text, null);
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

  // the page opened again (back from a title's page, from the history): nothing is busy any more
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    findButton.disabled = false;
    say(findButton, "");
  });

  // a link given (from another part of «Скачать») — its page at once; a name given (?q=) — searched at once; else the
  // covers of the last search by a name: open, the one picked marked, when the page is back from its title — folded
  // into one line when the page is opened anew
  if (box.dataset.autofind === "1" && field.value.trim()) {
    delete box.dataset.autofind;
    form.requestSubmit();
  } else if (box.dataset.autoname) {
    searchName(box.dataset.autoname);
  } else {
    fetch("/api/manhwa/search-state?id=last", { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => {
        if (!st.id || hitsId || !(st.titles || []).length || (st.age || 0) > 6 * 3600) return;
        const back = remembered();
        startHits(st.id, st.query || "");
        if (back && String(back.search) === String(st.id)) markPicked(back.url);
        showHits(st);
        const tile = picked && hitTiles.get(picked);
        let returned = false;          // back here by the history (from the title picked), not opened anew
        try {
          const entry = performance.getEntriesByType("navigation")[0];
          returned = !!entry && entry.type === "back_forward";
        } catch (_) { returned = false; }
        if (tile && returned) {
          tile.scrollIntoView({ block: "nearest" });
        } else {
          hitsBox.classList.add("folded");
          showFold();
        }
        if (!st.done) pollHits();
      }).catch(() => {});
  }
})();
