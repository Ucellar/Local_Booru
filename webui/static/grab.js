// Local Booru page «Граббер»: search booru sites by tags, the found posts as cards (one per file),
// the post view, downloading one or many, hiding, the tags of what was found, subscribing.
(() => {
  "use strict";

  const LB = window.LocalBooru;
  const section = document.getElementById("grab");
  if (!LB || !section) return;
  const { actions, postJson, say } = LB;
  const confirmed = LB.confirmed || (() => true);
  const root = document.documentElement;
  const data = JSON.parse((document.getElementById("grab-data") || {}).textContent || "{}");
  const form = section.querySelector('form[data-form="grab-search"]');
  const queryInput = form.elements.namedItem("q");
  const results = section.querySelector(".grab-results");
  const statusLine = section.querySelector(".grab-status");
  const pager = section.querySelector(".grab-pages");
  const pageNote = section.querySelector(".grab-page-note");
  const sizes = section.querySelector(".thumb-sizes");
  const pageSize = Number(data.pageSize) || 42;
  const bar = section.querySelector(".select-bar");
  const jobBox = section.querySelector(".grab-job");
  const viewer = section.querySelector(".grab-viewer");
  const tagBox = document.querySelector(".grab-tag-list");
  const OWNED = {
    file: ["есть", "Этот файл уже в галерее"],
    post: ["есть похожая", "Пост уже записан источником картинки из галереи (найден обратным поиском — файл может отличаться)"],
    trash: ["в корзине", "Файл в корзине — вернуть его можно там"],
  };
  let search = null;              // {id, query, more, pending, sites, skipped, shown}
  let cards = [];                 // all loaded results, in order
  let pages = [];                 // stable groups of cards, split at the end of each server batch
  let page = 1;
  const byKey = new Map();
  let current = -1;               // the card in the post view
  let busy = false;
  let jobTimer = 0;

  const media = (key, v) => `/grab/media?s=${encodeURIComponent(search.id)}&k=${encodeURIComponent(key)}&v=${v}`;
  const hasNextBatch = () => Boolean(search?.more && !search.pending?.length);
  const sitesChosen = () => Array.from(section.querySelectorAll('.site-picks input[name="site"]:checked')).map((b) => b.value);
  const hideOwned = () => { const b = section.querySelector('input[name="hide_owned"]'); return !!(b && b.checked); };

  function plural(n, one, few, many) {
    const d = n % 10, h = n % 100;
    if (d === 1 && h !== 11) return one;
    if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return few;
    return many;
  }

  // ── cards ──────────────────────────────────────────────────────────────
  function badge(text, title, cls) {
    const b = document.createElement("span");
    b.className = "badge" + (cls ? " " + cls : "");
    b.textContent = text;
    if (title) b.title = title;
    return b;
  }

  function fillBadges(box, c) {
    box.textContent = "";
    if (c.owned && OWNED[c.owned]) box.appendChild(badge(OWNED[c.owned][0], OWNED[c.owned][1], "owned"));
    if (c.video) box.appendChild(badge("видео", "", "kind"));
    else if (c.ext === "gif") box.appendChild(badge("gif", "", "kind"));
    if (c.sites.length > 1) {
      box.appendChild(badge(`${c.sites.length} ${plural(c.sites.length, "сайт", "сайта", "сайтов")}`, c.sites.join(", "), ""));
    }
    if (!c.original) box.appendChild(badge("без оригинала", "Сайт не отдаёт оригинал — скачать нельзя", "warn"));
    if (c.blocked && c.blocked.length) {
      box.appendChild(badge("в блоклисте", "Теги из блоклиста подписок и «Галереи (booru)»: " + c.blocked.join(", "),
        "warn"));
    }
  }

  function cardEl(c) {
    const art = document.createElement("article");
    art.className = "thumb" + (c.video ? " is-video" : "") + (c.tags.length ? "" : " untagged");
    art.dataset.id = c.key;
    art.dataset.owned = c.owned || "";
    const ratio = c.w && c.h ? Math.max(0.3, Math.min(3.5, c.w / c.h)) : 1;
    art.style.setProperty("--ar", ratio.toFixed(4));
    const link = document.createElement("a");
    link.className = "thumb-link";
    link.href = c.url;                      // the post on its site; a click opens the view here
    link.title = c.tags.slice(0, 60).map((t) => t[0]).join(" ");
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    const missing = document.createElement("span");
    missing.className = "grab-no-preview";
    missing.hidden = true;
    missing.textContent = "Нет превью";
    img.addEventListener("error", () => { img.hidden = true; missing.hidden = false; }, { once: true });
    img.src = media(c.key, "preview");
    const caption = document.createElement("span");
    caption.className = "thumb-caption";
    const lead = ["artist", "character", "copyright"].flatMap((cat) => c.tags.filter((t) => t[1] === cat));
    caption.textContent = (lead.length ? lead.slice(0, 3).map(([tag]) => tag).join(" · ") : c.tags[0]?.[0] || "без тегов")
      .replaceAll("_", " ");
    const badges = document.createElement("span");
    badges.className = "badges";
    fillBadges(badges, c);
    link.append(img, missing, caption, badges);
    const info = document.createElement("div");
    info.className = "thumb-info";
    if (c.w && c.h) {
      const size = document.createElement("span");
      size.className = "ti-size";
      size.textContent = `${c.w}×${c.h}`;
      info.appendChild(size);
    }
    const kind = document.createElement("span");
    kind.className = "ti-kind";
    kind.textContent = c.ext.toUpperCase() || (c.video ? "Видео" : "");
    info.appendChild(kind);
    art.append(link, info);
    return art;
  }

  function cardNode(key) {
    return results.querySelector(`.thumb[data-id="${CSS.escape(key)}"]`);
  }

  function addCards(list, round) {
    let tail = pages.includes(round.tail) ? round.tail : null;
    for (const c of list) {
      if (byKey.has(c.key)) continue;
      byKey.set(c.key, c);
      if (!tail || tail.length >= pageSize) {
        tail = [];
        pages.push(tail);
        if (!pages.includes(round.first)) round.first = tail;
      }
      tail.push(c);
      cards.push(c);
    }
    round.tail = tail;
  }

  function updateCards(list) {
    list.forEach((c) => {
      if (!byKey.has(c.key)) return;
      const saved = byKey.get(c.key);
      const changedSources = c.sites && c.sites.length > saved.sites.length;
      Object.assign(saved, c);
      const node = cardNode(c.key);
      if (node) {
        if (changedSources && !node.querySelector(".grab-no-preview").hidden) {
          node.replaceWith(cardEl(saved));
          LB.refreshSelection?.();
        } else {
          node.dataset.owned = saved.owned || "";
          fillBadges(node.querySelector(".badges"), saved);
        }
      }
    });
  }

  function removeCards(keys) {
    if (!keys.length) return;
    keys.forEach((key) => {
      byKey.delete(key);
      if (LB.selected) LB.selected.delete(key);
    });
    const visible = pages[page - 1]?.find((card) => byKey.has(card.key));
    for (const group of pages) {
      for (let i = group.length - 1; i >= 0; i--) {
        if (!byKey.has(group[i].key)) group.splice(i, 1);
      }
    }
    pages = pages.filter((group) => group.length);
    cards = pages.flat();
    if (visible) page = pages.findIndex((group) => group.includes(visible)) + 1;
  }

  function loadedPages() {
    return Math.max(1, pages.length);
  }

  function pageLink(number, text, title = "") {
    const link = document.createElement("a");
    link.href = "#grab-results";
    link.dataset.action = "grab-page";
    link.dataset.page = String(number);
    if (title) link.setAttribute("aria-label", title);
    link.textContent = text;
    return link;
  }

  function paintPager() {
    const last = loadedPages();
    pager.hidden = !search || (last === 1 && !hasNextBatch());
    if (pager.hidden) return;
    const links = [];
    if (page > 1) links.push(pageLink(page - 1, "‹", "Предыдущая страница"));
    let previous = 0;
    const start = Math.max(1, page - 2);
    const width = Math.min(last, page + 2) - start + 1;
    for (const n of new Set([1, ...Array.from({ length: width }, (_, index) => start + index), last])) {
      if (previous && n > previous + 1) {
        const gap = document.createElement("span");
        gap.className = "gap";
        gap.textContent = "…";
        links.push(gap);
      }
      if (n === page) {
        const currentPage = document.createElement("b");
        currentPage.setAttribute("aria-current", "page");
        currentPage.textContent = String(n);
        links.push(currentPage);
      } else links.push(pageLink(n, String(n)));
      previous = n;
    }
    if (page < last || hasNextBatch()) links.push(pageLink(page + 1, "›", "Следующая страница"));
    pager.replaceChildren(...links);
  }

  function paintPage() {
    page = Math.min(page, loadedPages());
    const wanted = (pages[page - 1] || []).map((c) => cardNode(c.key) || cardEl(c));
    wanted.forEach((node, index) => {
      if (results.children[index] !== node) results.insertBefore(node, results.children[index] || null);
    });
    while (results.children.length > wanted.length) results.lastElementChild.remove();
    LB.refreshSelection?.();
    paintPager();
    renderTags();
  }

  function pageMessage(text, error = false) {
    pageNote.hidden = !text;
    pageNote.textContent = text;
    pageNote.classList.toggle("tool-error", error);
  }

  // ── what was found: the status line and the tags ───────────────────────
  function renderStatus() {
    if (!search) { statusLine.textContent = ""; return; }
    const pending = new Set(search.pending || []);
    const parts = [`Найдено: ${cards.length}`];
    Object.values(search.sites || {}).forEach((s) => {
      if (pending.has(s.label)) parts.push(`${s.label} — жду ответа`);
      else if (s.error) parts.push(`${s.label}: ${s.error}`);
      else parts.push(`${s.label} ${s.posts}${s.done ? "" : "+"}`);
    });
    const sk = search.skipped || {};
    const hidden = [];
    if (sk.owned) hidden.push(`уже есть — ${sk.owned}`);
    if (sk.blocked) hidden.push(`блоклист — ${sk.blocked}`);
    if (sk.hidden) hidden.push(`скрытые — ${sk.hidden}`);
    if (hidden.length) parts.push("не показаны: " + hidden.join(", "));
    const next = parts.join(" · ");
    if (statusLine.textContent !== next) statusLine.textContent = next;
    bar.hidden = sizes.hidden = !cards.length;
  }

  function renderTags() {
    if (!tagBox) return;
    const counts = new Map();
    (pages[page - 1] || []).forEach((c) => c.tags.forEach(([name, cat]) => {
      const low = name.toLowerCase();
      const row = counts.get(low) || { name, cat, n: 0 };
      row.n += 1;
      counts.set(low, row);
    }));
    tagBox.textContent = "";
    if (!counts.size) {
      const p = document.createElement("p");
      p.className = "hint";
      p.textContent = search ? "У найденного нет тегов." : "Здесь появятся теги найденных постов.";
      tagBox.appendChild(p);
      return;
    }
    (data.categories || []).forEach(([cat, title]) => {
      const rows = [...counts.values()].filter((r) => (r.cat || "general") === cat).sort((a, b) => b.n - a.n).slice(0, 60);
      if (!rows.length) return;
      const h = document.createElement("h4");
      h.textContent = title;
      const ul = document.createElement("ul");
      ul.className = "tag-list";
      rows.forEach((r) => ul.appendChild(tagLine(r.name, cat, r.n)));
      tagBox.append(h, ul);
    });
  }

  function tagLine(name, cat, n) {
    const li = document.createElement("li");
    li.className = "tag-" + (cat || "general");
    const plus = tagButton("+", "Добавить к поиску", "grab-tag-add", name);
    const minus = tagButton("−", "Исключить из поиска", "grab-tag-exclude", name);
    const only = document.createElement("a");
    only.className = "tag-name";
    only.href = "/grab?q=" + encodeURIComponent(name);
    only.dataset.grabTag = name;
    only.textContent = name.replace(/_/g, " ");
    li.append(plus, minus, only);
    if (n != null) {
      const count = document.createElement("span");
      count.className = "tag-count";
      count.textContent = String(n);
      li.appendChild(count);
    }
    return li;
  }

  function tagButton(text, title, action, name) {
    const b = document.createElement("a");
    b.className = "tag-op";
    b.href = "#";
    b.title = title;
    b.textContent = text;
    b.dataset.action = action;
    b.dataset.tag = name;
    return b;
  }

  function searchFor(query) {
    queryInput.value = query.trim();
    runSearch();
  }

  // a tag name in the sidebar or the post view: a new search with it
  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-grab-tag]");
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    if (viewer.open) viewer.close();
    searchFor(link.dataset.grabTag);
  });

  // ── searching ──────────────────────────────────────────────────────────
  function applyBatch(result, round) {
    const viewed = viewer.open ? cards[current]?.key : null;
    const changed = result.cards.length || result.updated.length || result.removed.length;
    search = result.search;
    if (changed) {
      updateCards(result.updated);
      removeCards(result.removed);
      addCards(result.cards, round);
      paintPage();
    } else paintPager();
    renderStatus();
    if (viewed) {
      current = cards.findIndex((card) => card.key === viewed);
      if (current < 0) viewer.close();
      else {
        viewer.querySelector(".gv-prev").disabled = current === 0;
        viewer.querySelector(".gv-next").disabled = current === cards.length - 1 && !hasNextBatch();
      }
    }
  }

  function watchSearch(round, onUpdate) {
    if (!search.pending?.length) return;
    const id = search.id;
    const sameSearch = () => search?.id === id;
    const poll = async () => {
      if (!sameSearch()) return;
      try {
        const result = await postJson("/api/grab/progress", { s: id });
        if (!sameSearch()) return;
        applyBatch(result, round);
        onUpdate?.(result);
        if (search.pending?.length) setTimeout(poll, 800);
      } catch (err) {
        if (sameSearch()) {
          search.pending = [];
          search.more = false;
          renderStatus();
          paintPager();
          pageMessage(err.message, true);
        }
      }
    };
    setTimeout(poll, 800);
  }

  async function runSearch() {
    const query = queryInput.value.trim();
    const submit = form.querySelector('button[type="submit"]');
    if (!query) { statusLine.textContent = "Введите теги или ссылку на пост"; return; }
    if (busy) return;
    busy = true;
    submit.disabled = true;
    statusLine.textContent = "Ищу…";
    pageMessage("");
    try {
      const r = await postJson("/api/grab/search", { q: query, sites: sitesChosen(), hide_owned: hideOwned(), partial: true });
      page = 1;
      cards = [];
      pages = [];
      byKey.clear();
      results.replaceChildren();
      renderTags();
      if (LB.selected) LB.selected.clear();
      bar.querySelector(".action-status").textContent = "";
      const round = {};
      applyBatch(r, round);
      let opened = false;
      const summarize = (result) => {
        if (result.error_note) statusLine.textContent = result.error_note;
        else if (!cards.length && !search.pending?.length) {
          statusLine.textContent += hasNextBatch() ? " — попробуйте следующую страницу" : " — ничего не нашлось";
        } else if (!opened && search.link && cards.length === 1) {
          opened = true;
          openView(0);                                      // a link: its post at once
        }
      };
      summarize(r);
      watchSearch(round, summarize);
      const params = new URLSearchParams({ q: query, sites: sitesChosen().join(",") });
      history.replaceState(null, "", "/grab?" + params.toString());
    } catch (err) {
      statusLine.textContent = err.message;
    } finally {
      busy = false;
      submit.disabled = false;
    }
  }

  async function loadMore(advance = false) {
    if (!hasNextBatch() || busy) return false;
    busy = true;
    pageMessage("Ищу следующую страницу…");
    pager.setAttribute("aria-busy", "true");
    try {
      const r = await postJson("/api/grab/more", { s: search.id, partial: true });
      const round = {};
      applyBatch(r, round);
      const fromPage = page;
      const first = pages.indexOf(round.first) + 1;
      let awaitingPage = advance && !first;
      const note = () => {
        if (round.first || search.pending?.length) pageMessage("");
        else pageMessage(search.more ? "На сайтах ничего нового. Можно продолжить поиск." : "Больше результатов нет.");
      };
      note();
      watchSearch(round, () => {
        note();
        if (awaitingPage && round.first) {
          awaitingPage = false;
          if (page === fromPage && !viewer.open) goToPage(pages.indexOf(round.first) + 1);
        }
      });
      return first;
    } catch (err) {
      pageMessage(err.message, true);
      return false;
    } finally {
      busy = false;
      pager.removeAttribute("aria-busy");
    }
  }

  async function goToPage(number) {
    if (busy || !search || number < 1) return;
    if (number > loadedPages()) number = await loadMore(true);
    if (number && number !== page) {
      pageMessage("");
      page = number;
      paintPage();
      statusLine.scrollIntoView({ block: "start" });
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch();
  });

  const hideBox = section.querySelector('input[name="hide_owned"]');
  if (hideBox) {
    hideBox.addEventListener("change", async () => {
      try { await postJson("/api/grab/options", { hide_owned: hideBox.checked }); } catch (err) { /* only remembered */ }
      if (search) runSearch();
    });
  }

  // ── the post view ──────────────────────────────────────────────────────
  results.addEventListener("click", (event) => {
    const node = event.target.closest(".thumb");
    if (!node || root.classList.contains("selecting")) return;
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;   // the post on its site
    event.preventDefault();
    openView(cards.findIndex((c) => c.key === node.dataset.id));
  });

  let viewToken = 0;

  async function openView(index) {
    if (index < 0 || index >= cards.length) return;
    current = index;
    const c = cards[index];
    const cardPage = pages.findIndex((group) => group.includes(c)) + 1;
    if (page !== cardPage) {
      page = cardPage;
      paintPage();
    }
    const token = ++viewToken;              // a late answer for a card no longer shown is dropped
    const img = viewer.querySelector(".gv-media img");
    const note = viewer.querySelector(".gv-note");
    img.hidden = false;
    img.onerror = () => {
      if (token !== viewToken) return;
      img.hidden = true;
      note.hidden = false;
      note.textContent = "Превью недоступно. Пост можно открыть на сайте.";
    };
    img.src = media(c.key, c.video ? "preview" : "sample");
    note.hidden = !c.video;
    note.textContent = c.video ? "Видео: здесь только кадр — посмотреть можно на сайте или после скачивания." : "";
    viewer.querySelector("#gv-title").textContent = c.sites.join(", ") + (c.w && c.h ? ` · ${c.w}×${c.h}` : "");
    viewer.querySelector(".gv-prev").disabled = index === 0;
    viewer.querySelector(".gv-next").disabled = index === cards.length - 1 && !hasNextBatch();
    viewer.querySelector('[data-action="grab-download-one"]').disabled = !c.original || !!(c.blocked && c.blocked.length);
    const status = viewer.querySelector(".action-status");
    if (status) status.textContent = "";
    renderOwned(c);
    renderViewTags(c.tags.reduce((acc, [name, cat]) => {
      (acc[cat || "general"] = acc[cat || "general"] || []).push(name);
      return acc;
    }, {}));
    viewer.querySelector(".gv-sites").textContent = "";
    if (!viewer.open) viewer.showModal();
    try {
      const r = await fetch(`/api/grab/card?s=${encodeURIComponent(search.id)}&k=${encodeURIComponent(c.key)}`,
        { credentials: "same-origin" });
      const answer = await r.json();
      if (token !== viewToken || !answer.ok) return;
      renderSites(answer.entries);
      const groups = {};
      answer.groups.forEach(([cat, _title, names]) => { groups[cat] = names; });
      renderViewTags(groups);
    } catch (err) { /* the tags of the card stay */ }
  }

  function renderOwned(c) {
    const line = viewer.querySelector(".gv-owned");
    line.textContent = "";
    const blocked = c.blocked && c.blocked.length;
    line.hidden = !c.owned && !blocked;
    if (blocked) {
      line.append(`Теги из блоклиста подписок и «Галереи (booru)»: ${c.blocked.join(", ")} — такой пост не скачивается. `);
    }
    if (!c.owned) return;
    line.append(OWNED[c.owned] ? OWNED[c.owned][1] + ". " : "");
    if (c.image_id) {
      const a = document.createElement("a");
      a.href = "/post/" + c.image_id;
      a.textContent = "Открыть в галерее";
      line.appendChild(a);
    }
  }

  function renderSites(entries) {
    const ul = viewer.querySelector(".gv-sites");
    ul.textContent = "";
    entries.forEach((e) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = e.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = e.label;
      li.appendChild(a);
      const facts = [e.ext ? e.ext.toUpperCase() : "", e.w && e.h ? `${e.w}×${e.h}` : "", e.rating ? "рейтинг " + e.rating : "",
        e.original ? "" : "без оригинала"].filter(Boolean);
      if (facts.length) li.append(" · " + facts.join(" · "));
      ul.appendChild(li);
    });
  }

  function renderViewTags(groups) {
    const box = viewer.querySelector(".gv-tags");
    box.textContent = "";
    (data.categories || []).forEach(([cat, title]) => {
      const names = groups[cat];
      if (!names || !names.length) return;
      const h = document.createElement("h4");
      h.textContent = title;
      const ul = document.createElement("ul");
      ul.className = "tag-list";
      names.forEach((name) => ul.appendChild(tagLine(name, cat, null)));
      box.append(h, ul);
    });
  }

  viewer.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); actions["grab-prev"](); }
    if (event.key === "ArrowRight") { event.preventDefault(); actions["grab-next"](); }
  });
  viewer.addEventListener("click", (event) => {           // a click on the dark backdrop closes it
    if (event.target === viewer) viewer.close();
  });

  // ── downloading ────────────────────────────────────────────────────────
  async function download(el, keys) {
    if (!keys.length) return;
    el.disabled = true;
    try {
      const r = await postJson("/api/grab/download", { s: search.id, keys });
      say(el, `Скачиваю: ${r.count} ${plural(r.count, "файл", "файла", "файлов")}`);
      watchJob();
    } catch (err) {
      say(el, err.message, true);
    } finally {
      el.disabled = false;
    }
  }

  async function watchJob() {
    clearTimeout(jobTimer);
    try {
      const r = await fetch("/api/subs/state?after=999999999", { credentials: "same-origin" });
      const st = await r.json();
      const running = ["starting", "running", "waiting", "stopping"].includes(st.status);
      jobBox.hidden = !st.id || st.kind !== "grab";          // the grabber's own downloads only
      if (st.id && st.kind === "grab") {
        jobBox.querySelector(".grab-job-title").textContent = st.label || "";
        let text = running ? `идёт, скачано ${st.downloaded || 0}` : `готово, скачано ${st.downloaded || 0}`;
        if (st.status === "waiting") text = "ждёт ответа о большой загрузке — в окне программы или на странице «Подписки»";
        jobBox.querySelector(".grab-job-status").textContent = text;
      }
      if (running && st.kind === "grab") {
        jobTimer = setTimeout(watchJob, 1000);
      } else if (search && st.kind === "grab") {
        refreshMarks();
      }
    } catch (err) {
      jobTimer = setTimeout(watchJob, 4000);
    }
  }

  async function refreshMarks() {
    const id = search?.id;
    if (!id) return;
    try {
      const marks = await postJson("/api/grab/owned", { s: id });
      if (id !== search?.id) return;
      Object.entries(marks.marks || {}).forEach(([key, m]) => updateCards([{ key, owned: m.owned, image_id: m.image_id }]));
      if (viewer.open && current >= 0 && cards[current]) renderOwned(cards[current]);
    } catch (err) { /* the search is gone (a new one, or two hours): the marks stay as they were */ }
  }

  async function hide(el, keys) {
    if (!keys.length) return;
    const id = search.id;
    try {
      const r = await postJson("/api/grab/hide", { s: id, keys });
      if (id !== search?.id) return;
      removeCards(keys);
      renderStatus();
      paintPage();
      say(el, `Скрыто: ${r.hidden}`);
    } catch (err) {
      say(el, err.message, true);
    }
  }

  Object.assign(actions, {
    "grab-page": (el) => goToPage(Number(el.dataset.page)),
    "grab-tag-add"(el) {
      searchFor(queryInput.value.trim() + " " + el.dataset.tag);
    },
    "grab-tag-exclude"(el) {
      searchFor(queryInput.value.trim() + " -" + el.dataset.tag);
    },
    "grab-viewer-close": () => viewer.close(),
    async "grab-next"() {
      if (current < cards.length - 1) { openView(current + 1); return; }
      const key = cards[current]?.key;
      if (!key || !await loadMore() || !viewer.open) return;
      const still = cards.findIndex((c) => c.key === key);
      if (still >= 0 && still < cards.length - 1) openView(still + 1);
    },
    "grab-prev"() {
      if (current > 0) openView(current - 1);
    },
    "grab-download-one"(el) {
      if (current >= 0) download(el, [cards[current].key]);
    },
    "grab-download-selected": (el) => download(el, [...(LB.selected || [])]),
    async "grab-hide-one"(el) {
      if (current < 0) return;
      const key = cards[current].key;
      await hide(el, [key]);
      if (!cards.length) { viewer.close(); return; }
      openView(Math.min(current, cards.length - 1));
    },
    "grab-hide-selected"(el) {
      const keys = [...(LB.selected || [])];
      if (!confirmed(el, `Скрыть ${keys.length} навсегда? Нажмите ещё раз`)) return;
      hide(el, keys);
    },
    async "grab-subscribe"(el) {
      if (!search) return;
      try {
        const r = await postJson("/api/grab/subscribe", { s: search.id });
        say(el, r.existing ? "Такая подписка уже есть — «Подписки»" : "Подписка создана — «Подписки»");
      } catch (err) {
        say(el, err.message, true);
      }
    },
  });

  watchJob();
  if (section.dataset.query) runSearch();
})();
