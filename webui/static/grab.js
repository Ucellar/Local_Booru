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
  const moreBox = section.querySelector(".grab-more");
  const bar = section.querySelector(".select-bar");
  const jobBox = section.querySelector(".grab-job");
  const viewer = section.querySelector(".grab-viewer");
  const tagBox = document.querySelector(".grab-tag-list");
  const OWNED = {
    file: ["есть", "Этот файл уже в галерее"],
    post: ["есть похожая", "Пост уже записан источником картинки из галереи (найден обратным поиском — файл может отличаться)"],
    trash: ["в корзине", "Файл в корзине — вернуть его можно там"],
  };
  let search = null;              // {id, query, more, sites, skipped, shown}
  let cards = [];                 // shown, in order
  const byKey = new Map();
  let current = -1;               // the card in the post view
  let busy = false;
  let jobTimer = 0;

  const media = (key, v) => `/grab/media?s=${encodeURIComponent(search.id)}&k=${encodeURIComponent(key)}&v=${v}`;
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
      box.appendChild(badge("в блоклисте", "Теги из блоклиста граббера и подписок: " + c.blocked.join(", "), "warn"));
    }
  }

  function cardEl(c) {
    const art = document.createElement("article");
    art.className = "thumb" + (c.video ? " is-video" : "");
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
    img.src = media(c.key, "preview");
    link.appendChild(img);
    const badges = document.createElement("span");
    badges.className = "badges";
    fillBadges(badges, c);
    link.appendChild(badges);
    art.appendChild(link);
    return art;
  }

  function cardNode(key) {
    return results.querySelector(`.thumb[data-id="${CSS.escape(key)}"]`);
  }

  function addCards(list) {
    const frag = document.createDocumentFragment();
    list.forEach((c) => {
      if (byKey.has(c.key)) return;
      byKey.set(c.key, c);
      cards.push(c);
      frag.appendChild(cardEl(c));
    });
    results.appendChild(frag);
  }

  function updateCards(list) {
    list.forEach((c) => {
      if (!byKey.has(c.key)) return;
      Object.assign(byKey.get(c.key), c);
      const node = cardNode(c.key);
      if (node) {
        node.dataset.owned = byKey.get(c.key).owned || "";
        fillBadges(node.querySelector(".badges"), byKey.get(c.key));
      }
    });
  }

  function removeCards(keys) {
    keys.forEach((key) => {
      byKey.delete(key);
      const node = cardNode(key);
      if (node) node.remove();
      if (LB.selected) LB.selected.delete(key);
    });
    cards = cards.filter((c) => byKey.has(c.key));
    if (LB.refreshSelection) LB.refreshSelection();
  }

  // ── what was found: the status line and the tags ───────────────────────
  function renderStatus() {
    if (!search) { statusLine.textContent = ""; return; }
    const parts = [`Найдено: ${cards.length}`];
    Object.values(search.sites || {}).forEach((s) => {
      if (s.error) parts.push(`${s.label}: ${s.error}`);
      else parts.push(`${s.label} ${s.posts}${s.done ? "" : "+"}`);
    });
    const sk = search.skipped || {};
    const hidden = [];
    if (sk.owned) hidden.push(`уже есть — ${sk.owned}`);
    if (sk.blocked) hidden.push(`блоклист — ${sk.blocked}`);
    if (sk.hidden) hidden.push(`скрытые — ${sk.hidden}`);
    if (hidden.length) parts.push("не показаны: " + hidden.join(", "));
    statusLine.textContent = parts.join(" · ");
    moreBox.hidden = !search.more;
    bar.hidden = !cards.length;
  }

  function renderTags() {
    if (!tagBox) return;
    const counts = new Map();
    cards.forEach((c) => c.tags.forEach(([name, cat]) => {
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
  async function runSearch() {
    const query = queryInput.value.trim();
    const submit = form.querySelector('button[type="submit"]');
    if (!query) { say(submit, "Введите теги или ссылку на пост", true); return; }
    if (busy) return;
    busy = true;
    submit.disabled = true;
    statusLine.textContent = "Ищу…";
    try {
      const r = await postJson("/api/grab/search", { q: query, sites: sitesChosen(), hide_owned: hideOwned() });
      search = r.search;
      cards = [];
      byKey.clear();
      results.textContent = "";
      if (LB.selected) LB.selected.clear();
      if (LB.refreshSelection) LB.refreshSelection();
      addCards(r.cards);
      renderStatus();
      renderTags();
      if (r.error_note) statusLine.textContent = r.error_note;
      else if (!cards.length) statusLine.textContent += search.more ? " — нажмите «Ещё»" : " — ничего не нашлось";
      const params = new URLSearchParams({ q: query, sites: sitesChosen().join(",") });
      history.replaceState(null, "", "/grab?" + params.toString());
      if (search.link && cards.length === 1) openView(0);       // a link: its post at once
    } catch (err) {
      statusLine.textContent = err.message;
    } finally {
      busy = false;
      submit.disabled = false;
    }
  }

  async function loadMore(el) {
    if (!search || busy) return;
    busy = true;
    el.disabled = true;
    try {
      const r = await postJson("/api/grab/more", { s: search.id });
      search = r.search;
      addCards(r.cards);
      updateCards(r.updated);
      removeCards(r.removed);
      renderStatus();
      renderTags();
      if (!r.cards.length && search.more) say(el, "На этой странице ничего нового — ещё раз?");
    } catch (err) {
      say(el, err.message, true);
    } finally {
      busy = false;
      el.disabled = false;
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
    const token = ++viewToken;              // a late answer for a card no longer shown is dropped
    const img = viewer.querySelector(".gv-media img");
    const note = viewer.querySelector(".gv-note");
    img.removeAttribute("src");
    img.src = media(c.key, c.video ? "preview" : "sample");
    note.hidden = !c.video;
    note.textContent = c.video ? "Видео: здесь только кадр — посмотреть можно на сайте или после скачивания." : "";
    viewer.querySelector("#gv-title").textContent = c.sites.join(", ") + (c.w && c.h ? ` · ${c.w}×${c.h}` : "");
    viewer.querySelector(".gv-prev").disabled = index === 0;
    viewer.querySelector(".gv-next").disabled = index === cards.length - 1 && !(search && search.more);
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
    if (blocked) line.append(`Теги из блоклиста граббера и подписок: ${c.blocked.join(", ")} — такой пост не скачивается. `);
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
    try {
      const marks = await postJson("/api/grab/owned", { s: search.id });
      Object.entries(marks.marks || {}).forEach(([key, m]) => updateCards([{ key, owned: m.owned, image_id: m.image_id }]));
      if (viewer.open && current >= 0 && cards[current]) renderOwned(cards[current]);
    } catch (err) { /* the search is gone (a new one, or two hours): the marks stay as they were */ }
  }

  async function hide(el, keys) {
    if (!keys.length) return;
    try {
      const r = await postJson("/api/grab/hide", { s: search.id, keys });
      removeCards(keys);
      renderStatus();
      renderTags();
      say(el, `Скрыто: ${r.hidden}`);
    } catch (err) {
      say(el, err.message, true);
    }
  }

  Object.assign(actions, {
    "grab-more": (el) => loadMore(el),
    "grab-tag-add"(el) {
      searchFor(queryInput.value.trim() + " " + el.dataset.tag);
    },
    "grab-tag-exclude"(el) {
      searchFor(queryInput.value.trim() + " -" + el.dataset.tag);
    },
    "grab-viewer-close": () => viewer.close(),
    async "grab-next"() {
      if (current < cards.length - 1) { openView(current + 1); return; }
      const more = section.querySelector('[data-action="grab-more"]');
      if (search && search.more && more) {
        const before = cards.length;
        await loadMore(more);
        if (cards.length > before) openView(before);
      }
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
