// Local Booru page «Настройки» (v5.44.161, the mockup approved 11.11.2026): the sections on the left, one shown at
// a time (the address says which: #look, #manga…; a block's anchor #set-… opens its section; the tabs' addresses of
// before — #tab-… — the sections their blocks went to); the field «Найти настройку» leaves the rows with the typed
// words — of every section — and each row can be changed right there; a change in any section shows the bar
// «Изменено: … — Отменить / Сохранить»: it saves every changed form (each on its own, as the program checks them;
// the forms of «Парсер и сайты» say where they are saved — data-save); «Выбрать…» asks the program window for a
// folder or a file; actions of a section (a copy now, a pairing code, …) are buttons with data-action="settings-act".
(() => {
  "use strict";

  const LB = window.LocalBooru;
  const page = document.getElementById("settings");
  if (!LB || !page) return;
  const { actions, postJson, say } = LB;
  const confirmed = LB.confirmed || (() => true);

  function valuesOf(form) {
    const out = {};
    Array.from(form.elements).forEach((el) => {
      if (!el.name) return;
      if (el.type === "radio") { if (el.checked) out[el.name] = el.value; return; }
      out[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    return out;
  }

  // ── the sections ─────────────────────────────────────────────────────
  const nav = page.querySelector(".sx-nav");
  const panes = Array.from(page.querySelectorAll(".sx-pane[data-pane]"));
  const links = nav ? Array.from(nav.querySelectorAll("a[data-pane]")) : [];
  let oldTabs = {};
  try { oldTabs = JSON.parse(page.dataset.oldTabs || "{}"); } catch (_) { oldTabs = {}; }
  let shownPane = "";

  function showPane(id, { remember = true } = {}) {
    if (!panes.some((p) => p.dataset.pane === id)) return false;
    shownPane = id;
    panes.forEach((p) => { p.hidden = p.dataset.pane !== id && p.dataset.pane !== "other"; });
    links.forEach((a) => {
      const on = a.dataset.pane === id;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    page.dataset.pane = id;
    if (remember) {
      try { localStorage.setItem("lb.settings-pane", id); } catch (_) { /* only this time */ }
      // the address says which section is shown — a reload opens the same one; opened by a section's own address
      // (/sites) and switched to another, the page is «Настройки» at that one
      if (history.replaceState) history.replaceState(null, "", "/settings#" + id);
    }
    return true;
  }

  function paneOf(el) {
    const pane = el && el.closest(".sx-pane[data-pane]");
    return pane ? pane.dataset.pane : "";
  }

  function openFromAddress() {
    let hash = "";
    try { hash = decodeURIComponent(location.hash || "").slice(1); } catch (_) { hash = ""; }
    if (!hash) return false;
    if (hash.startsWith("tab-")) return showPane(oldTabs[hash.slice(4)] || hash.slice(4), { remember: false });
    if (showPane(hash, { remember: false })) return true;
    const target = document.getElementById(hash);
    if (target && paneOf(target)) {
      showPane(paneOf(target), { remember: false });
      target.scrollIntoView({ block: "start" });
      return true;
    }
    return false;
  }

  if (panes.length) {
    if (!openFromAddress()) {
      let first = page.dataset.open || panes[0].dataset.pane;
      if (location.pathname === "/settings") {          // the section seen last (a page of a section opens its own)
        try { first = localStorage.getItem("lb.settings-pane") || first; } catch (_) { /* the first one */ }
      }
      if (!showPane(first, { remember: false })) showPane(panes[0].dataset.pane, { remember: false });
    }
    if (nav) {
      nav.addEventListener("click", (event) => {
        const a = event.target.closest("a[data-pane]");
        if (!a) return;
        event.preventDefault();
        stopFinding();
        showPane(a.dataset.pane);
        window.scrollTo({ top: 0 });
      });
      // ↑ ↓ (Home, End) walk the sections as a list; Enter opens the one in focus as any link
      nav.addEventListener("keydown", (event) => {
        const all = Array.from(nav.querySelectorAll("a"));
        const at = all.indexOf(document.activeElement);
        const to = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: all.length - 1 }[event.key];
        if (at < 0 || to === undefined || !all[to]) return;
        event.preventDefault();
        all[to].focus();
      });
    }
    // an address clicked while a setting is being looked for (a row that points at a section): the search ends,
    // then the section opens — its place is counted on the page as it is without the search
    window.addEventListener("hashchange", () => { stopFinding(); openFromAddress(); });
  }

  // ── one «Сохранить» for the page: the changed forms ────────────────────
  const forms = Array.from(page.querySelectorAll('form[data-form="settings"], form[data-save]'));
  const bar = page.querySelector(".settings-savebar");
  const saved = new Map();              // form → its values as last saved (or as the page came)

  function valuesKey(form) { return JSON.stringify(valuesOf(form)); }
  forms.forEach((form) => saved.set(form, valuesOf(form)));

  function dirtyForms() { return forms.filter((form) => valuesKey(form) !== JSON.stringify(saved.get(form))); }

  // the names of the settings changed (the rows' own names)
  function dirtyNames(dirty) {
    const names = [];
    dirty.forEach((form) => {
      const now = valuesOf(form), was = saved.get(form) || {};
      Object.keys(now).forEach((key) => {
        if (JSON.stringify(now[key]) === JSON.stringify(was[key])) return;
        const row = form.querySelector(`.sx-row[data-key="${CSS.escape(key)}"]`);
        const name = row ? row.querySelector(".sx-name") : null;
        const field = form.querySelector(`[name="${CSS.escape(key)}"]`);       // a field that is not a row: its label
        names.push(name ? name.textContent.trim() : (field && field.getAttribute("aria-label")) || key);
      });
    });
    return names;
  }

  let flash = "";                        // what the bar says for a moment after saving
  let flashTimer = 0;

  function refreshBar() {
    const dirty = dirtyForms();
    forms.forEach((form) => form.closest(".settings-block").classList.toggle("changed", dirty.includes(form)));
    links.forEach((a) => a.classList.toggle("changed", dirty.some((form) => paneOf(form) === a.dataset.pane)));
    if (!bar) return;
    bar.hidden = dirty.length === 0 && !flash;
    bar.classList.toggle("saved", dirty.length === 0 && !!flash);
    bar.querySelector("[data-bar-text]").textContent = dirty.length ? "Изменено:" : flash;
    const names = dirtyNames(dirty);
    bar.querySelector("[data-dirty-list]").textContent = names.length > 4 ? names.slice(0, 4).join(", ") + ` и ещё ${names.length - 4}`
      : names.join(", ");
    bar.querySelectorAll("button").forEach((b) => { b.hidden = dirty.length === 0; });
    if (dirty.length) {
      const status = bar.querySelector(".action-status");
      if (status && flash) status.textContent = "";
      flash = "";
    }
  }

  function flashSaved(text) {
    flash = text;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { flash = ""; refreshBar(); }, 2500);
  }

  function setValues(form, values) {
    Array.from(form.elements).forEach((el) => {
      if (!el.name || !(el.name in values)) return;
      const was = el.type === "radio" || el.type === "checkbox" ? el.checked : el.value;
      if (el.type === "radio") el.checked = String(values[el.name]) === el.value;
      else if (el.type === "checkbox") el.checked = !!values[el.name];
      else el.value = values[el.name];
      // what follows a field on the page (a site's address in its row, …) hears of the change as if it were typed
      if (was !== (el.type === "radio" || el.type === "checkbox" ? el.checked : el.value)) {
        el.dispatchEvent(new Event(el.type === "text" || el.type === "number" ? "input" : "change", { bubbles: true }));
      }
    });
  }

  // the fields the program refused, each with its words ({name: words}); none — the marks of before go
  function markWrong(form, wrong) {
    form.querySelectorAll('[aria-invalid="true"]').forEach((el) => el.removeAttribute("aria-invalid"));
    form.querySelectorAll(".field-wrong").forEach((note) => note.remove());
    Object.keys(wrong || {}).forEach((name) => {
      const field = form.querySelector(`[name="${CSS.escape(name)}"]`);
      if (!field) return;
      field.setAttribute("aria-invalid", "true");
      const note = document.createElement("small");
      note.className = "field-wrong";
      note.textContent = wrong[name];
      field.insertAdjacentElement("afterend", note);
    });
  }

  function showError(form, text, field) {
    const box = form.querySelector(".form-error");
    if (box) { box.textContent = text; box.hidden = !text; }
    if (!text) return;
    stopFinding();
    showPane(paneOf(form));
    const input = field ? form.querySelector(`[name="${CSS.escape(field)}"]`) : null;
    for (let fold = input && input.closest("details"); fold; fold = fold.parentElement.closest("details")) fold.open = true;
    if (input && input.focus) input.focus();
    else form.closest(".settings-block").scrollIntoView({ block: "center" });
  }

  async function saveAll(el) {
    const dirty = dirtyForms();
    if (!dirty.length) { refreshBar(); return true; }
    const button = bar ? bar.querySelector('[data-action="settings-save-all"]') : null;
    if (button) button.disabled = true;
    let ok = 0;
    let reload = false;                     // this page's own look was changed («Оформление»)
    try {
      for (const form of dirty) {
        showError(form, "");
        markWrong(form, null);
        if (!form.checkValidity()) {          // a number outside its bounds: the browser says what is wrong
          stopFinding();
          showPane(paneOf(form));
          form.reportValidity();
          say(el || button, (ok ? `Сохранено разделов: ${ok}; ` : "") + "исправьте отмеченное поле", true);
          return false;
        }
        try {
          const values = valuesOf(form);
          // a form of «Парсер и сайты» is saved where it says (its own address and its own shape)
          const reply = form.dataset.save ? await postJson(form.dataset.save, values)
            : await postJson("/api/settings/save", { section: form.dataset.section, values });
          reload = reload || !!(reply && reply.reload);
          saved.set(form, values);
          if (reply && reply.values) {          // as the program keeps them («site.org» became «https://site.org»)
            setValues(form, reply.values);
            saved.set(form, valuesOf(form));
          }
          ok += 1;
          form.querySelectorAll('input[type="password"][name]').forEach((input) => {   // a key is not shown back
            if (input.value) {
              input.value = "";
              input.placeholder = form.dataset.save ? "ключ сохранён — оставьте пустым, чтобы не менять"
                : "сохранено — оставьте пустым, чтобы не менять";
              const v = saved.get(form);
              v[input.name] = "";
            }
          });
        } catch (err) {
          markWrong(form, err.data && err.data.wrong);
          showError(form, err.message, err.data && err.data.field);
          say(el || button, (ok ? `Сохранено разделов: ${ok}; ` : "") + "не сохранено: " + err.message, true);
          return false;
        }
      }
      flashSaved(ok === 1 ? "Сохранено" : `Сохранено разделов: ${ok}`);
      if (reload) setTimeout(() => location.reload(), 300);
      return true;
    } finally {
      if (button) button.disabled = false;
      refreshBar();
    }
  }

  forms.forEach((form) => {
    form.addEventListener("input", refreshBar);
    form.addEventListener("change", refreshBar);
    form.addEventListener("submit", (event) => {         // Enter in a field: the same as «Сохранить»
      event.preventDefault();
      saveAll(bar ? bar.querySelector('[data-action="settings-save-all"]') : null);
    });
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirtyForms().length) { event.preventDefault(); event.returnValue = ""; }
  });

  actions["settings-save-all"] = (el) => { saveAll(el); };
  actions["settings-revert"] = (el) => {
    dirtyForms().forEach((form) => { setValues(form, saved.get(form)); showError(form, ""); markWrong(form, null); });
    refreshBar();
    say(el, "Изменения отменены");
  };
  // «изменить по страницам» of «Оформление»: the lists of the pages
  actions["looks-pages"] = (el) => {
    const box = document.getElementById("looks-pages");
    if (!box) return;
    box.hidden = !box.hidden;
    el.setAttribute("aria-expanded", box.hidden ? "false" : "true");
  };

  // ── «Найти настройку»: the rows with the typed words, of every section ──
  const findField = nav ? nav.querySelector('.sx-search input[type="search"]') : null;
  const found = page.querySelector("[data-found]");       // «Найдено: N» above what was found
  const also = page.querySelector("[data-also]");         // below it: what is not a setting but is looked for here
  const plain = (text) => String(text || "").toLowerCase().replace(/ё/g, "е");   // «ё» is typed as «е» as often as not
  // what can be found: the rows of settings, and what says by which words it is found (data-find: a site of manga —
  // by its name and addresses, not by the words every site's row has)
  const FOUND = ".sx-row:not([data-miss]), [data-find]:not([data-miss])";
  const rows = Array.from(page.querySelectorAll(".sx-row, [data-find]")).map((row) => {
    const block = row.closest(".sx-block, .sx-pointers");
    const pane = row.closest(".sx-pane");
    const where = [pane ? pane.dataset.title : "", block ? block.dataset.title : ""]
      .filter((part, i, all) => part && all.indexOf(part) === i);
    const own = row.hasAttribute("data-find");
    const what = own ? row : row.querySelector(".sx-what");
    if (what && where.length && !what.querySelector(":scope > .sx-where")) {   // where it is: said above it when found
      const line = document.createElement("small");
      line.className = "sx-where";
      line.textContent = where.join(" → ");
      what.prepend(line);
    }
    if (own) {
      const name = row.querySelector(".ms-name");
      return { row, text: plain((name ? name.textContent : "") + " " + row.dataset.find + " " + where.join(" ")) };
    }
    const options = Array.from(row.querySelectorAll("option, .sx-seg span")).map((o) => o.textContent).join(" ");
    return { row, text: plain((what ? what.textContent : row.textContent) + " " + options) };
  });
  const marked = [];
  const unfolded = [];                   // «Подробнее» opened by the search: the words were found in it

  function unmark() {
    marked.splice(0).forEach((mark) => {
      const parent = mark.parentNode;
      if (!parent) return;
      parent.replaceChild(document.createTextNode(mark.textContent), mark);
      parent.normalize();
    });
    unfolded.splice(0).forEach((more) => { more.open = false; });
  }

  function markIn(el, words) {            // the typed words marked in a row's name and its line
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach((node) => {
      const text = plain(node.nodeValue);
      let at = -1, len = 0;
      words.forEach((word) => {
        const i = text.indexOf(word);
        if (i >= 0 && (at < 0 || i < at)) { at = i; len = word.length; }
      });
      if (at < 0) return;
      const rest = node.splitText(at);
      rest.splitText(len);
      const mark = document.createElement("mark");
      rest.parentNode.replaceChild(mark, rest);
      mark.appendChild(rest);
      marked.push(mark);
    });
  }

  function stopFinding() {
    if (!page.hasAttribute("data-finding")) return;
    page.removeAttribute("data-finding");
    unmark();
    page.querySelectorAll("[data-miss]").forEach((el) => el.removeAttribute("data-miss"));
    if (found) found.hidden = true;
    if (also) also.hidden = true;
    if (findField) findField.value = "";
    // asked by the address (/settings?q=…): the words leave it with the search
    if (location.search && history.replaceState) history.replaceState(null, "", location.pathname + location.hash);
    showPane(shownPane || panes[0].dataset.pane, { remember: false });
  }

  function find() {
    const words = plain(findField.value).split(/\s+/).filter(Boolean);
    if (!words.length) { stopFinding(); return; }
    unmark();
    page.setAttribute("data-finding", words.join(" "));       // (the words looked for now: the checks wait for them)
    let count = 0;
    rows.forEach(({ row, text }) => {
      const fits = words.every((word) => text.includes(word));
      if (fits) { row.removeAttribute("data-miss"); count += 1; } else row.setAttribute("data-miss", "1");
      if (!fits) return;
      row.querySelectorAll(".sx-name, .sx-hint, .ms-name, .ms-addr").forEach((el) => markIn(el, words));
      // a word that is only in the long explanation: «Подробнее» opens, the word is marked there
      const more = row.querySelector(".sx-more");
      const seen = plain(Array.from(row.querySelectorAll(".sx-name, .sx-hint")).map((el) => el.textContent).join(" "));
      if (more && !more.open && words.some((word) => !seen.includes(word) && plain(more.textContent).includes(word))) {
        more.open = true;
        unfolded.push(more);
        more.querySelectorAll("p").forEach((el) => markIn(el, words));
      }
    });
    // a group, a block, a section with nothing found is not shown at all
    page.querySelectorAll(".sx-group, .sx-pages-edit").forEach((group) => {
      if (group.querySelector(FOUND)) group.removeAttribute("data-miss");
      else group.setAttribute("data-miss", "1");
    });
    page.querySelectorAll(".sx-block").forEach((block) => {
      if (block.querySelector(FOUND)) block.removeAttribute("data-miss");
      else block.setAttribute("data-miss", "1");
    });
    panes.forEach((pane) => {
      pane.hidden = false;
      if (pane.querySelector(FOUND)) pane.removeAttribute("data-miss");
      else pane.setAttribute("data-miss", "1");
    });
    links.forEach((a) => { a.classList.remove("on"); a.removeAttribute("aria-current"); });
    if (found) {
      found.hidden = false;
      found.querySelector("[data-found-title]").textContent = count ? `Найдено: ${count}` : "Ничего не найдено";
      found.querySelector("[data-found-none]").hidden = count > 0;
    }
    if (also) also.hidden = !also.querySelector(FOUND);
  }

  if (findField) {
    let timer = 0;
    findField.closest("form").addEventListener("submit", (event) => { event.preventDefault(); find(); });
    findField.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(find, 120); });
    findField.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && findField.value) { event.preventDefault(); stopFinding(); }
    });
    if (findField.value.trim()) find();          // asked from another page of the frame (/settings?q=…)
  }

  // a settings profile: the file goes as it is (the server checks it before anything changes)
  const profile = page.querySelector('form[data-form="profile-import"]');
  if (profile) {
    profile.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = profile.querySelector('button[type="submit"]');
      const file = profile.elements.namedItem("profile").files[0];
      if (!file) { say(button, "Выберите файл профиля", true); return; }
      if (file.size > 10 * 1024 * 1024) { say(button, "Файл больше 10 МБ — это не профиль настроек", true); return; }
      if (!confirmed(button, button.dataset.ask)) return;
      button.disabled = true;
      try {
        const r = await fetch("/api/settings/profile", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/octet-stream" }, body: file,
        });
        let data = {};
        try { data = await r.json(); } catch (_) { data = {}; }
        if (!r.ok || data.ok === false) throw new Error(data.error || ("Сервер ответил " + r.status));
        say(button, data.message || "Загружено");
        if (data.reload) setTimeout(() => location.reload(), 1500);
      } catch (err) {
        say(button, err.message, true);
      } finally {
        button.disabled = false;
      }
    });
  }

  // (v5.44.113) «Комиксы и манга — в папку манги → Проверить сейчас»: its journal; at its end — the counts anew
  const comicJob = page.querySelector("[data-comic-job]");
  let comicWatcher = null;
  if (comicJob && LB.watchTask) {
    const button = comicJob.parentElement.querySelector('[data-act="comic_manga_check"]');
    comicWatcher = LB.watchTask(comicJob, "/api/settings/comic/state", {
      onState(st, busy) { if (button) button.disabled = busy; },
      onEnd(st) {
        if (st.status === "failed") { say(button, st.error || "Не получилось — см. журнал", true); return; }
        say(button, st.status === "stopped" ? "Остановлено" : "Готово — обновляю");
        if (st.status !== "stopped") setTimeout(() => location.reload(), 2500);
      },
    });
  }

  // «Брак: фото или рисунок»: one task of the page at a time (install, download, the check, Брак again), its journal
  const visualJob = page.querySelector("[data-visual-job]");
  if (visualJob && LB.watchTask) {
    const box = visualJob.closest(".settings-block");
    const stopButton = box.querySelector('[data-action="visual-act"][data-act="stop"]');
    const starters = box.querySelectorAll('.tool-actions [data-action="visual-act"]:not([data-act="stop"])');
    const watcher = LB.watchTask(visualJob, "/api/visual/state", {
      onState(st, busy) {
        stopButton.hidden = !busy;
        starters.forEach((b) => { b.disabled = busy; });
      },
      onEnd(st) {
        if (st.status === "failed") { say(stopButton, st.error || "Не получилось — см. журнал", true); return; }
        say(stopButton, st.status === "stopped" ? "Остановлено — обновляю" : "Готово — обновляю");
        setTimeout(() => location.reload(), 1500);
      },
    });
    actions["visual-act"] = async (el) => {
      if (el.dataset.ask && !confirmed(el, el.dataset.ask)) return;
      el.disabled = true;
      try {
        const r = await postJson("/api/visual/act", { act: el.dataset.act, model: el.dataset.model || "" });
        say(el, r.message || "Запущено");
        if (r.reload) setTimeout(() => location.reload(), 700);
        else if (r.id) watcher.started();
      } catch (err) {
        say(el, err.message, true);
      } finally {
        el.disabled = false;
      }
    };
  }

  // «Перенос архива» (v5.36): the copy, its stop and cancel, the old copy's removal — one task of the page at a time;
  // when it ends the page shows what is next (its row is drawn by the server)
  const moveJob = page.querySelector("[data-move-job]");
  let moveWatcher = null;
  if (moveJob && LB.watchTask) {
    const stops = page.querySelectorAll('[data-action="settings-act"][data-act="move_stop"]');
    const starters = page.querySelectorAll('[data-act="move_continue"], [data-act="move_cancel"], '
      + '[data-act="move_delete_old"], [data-action="settings-move"], [data-action="settings-restart"]');
    moveWatcher = LB.watchTask(moveJob, "/api/settings/move/state", {
      onState(st, busy) {
        stops.forEach((b) => { b.hidden = !busy; });
        starters.forEach((b) => { b.disabled = busy; });
      },
      onEnd(st) {
        const failed = st.status === "failed";
        if (failed) say(stops[0] || starters[0] || moveJob, st.error || "Не получилось — см. журнал", true);
        setTimeout(() => location.reload(), failed ? 4000 : 1500);
      },
    });
  }

  Object.assign(actions, {
    // the program window asks for the folder the archive goes to; the copy starts at once (its journal above)
    async "settings-move"(el) {
      try {
        await postJson("/api/app/choose-move-target", {});
        say(el, "Выберите папку в открывшемся окне");
      } catch (err) {
        say(el, err.message, true);
      }
    },
    // the copy is done: the program closes and opens again — the move ends while it starts
    async "settings-restart"(el) {
      if (el.dataset.ask && !confirmed(el, el.dataset.ask)) return;
      el.disabled = true;
      try {
        await postJson("/api/app/restart-app", {});
        say(el, "Программа перезапускается…");
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },
    async "settings-choose"(el) {
      try {
        await postJson("/api/app/choose-path", { key: el.dataset.key });
        say(el, "Выберите в открывшемся окне");
      } catch (err) {
        say(el, err.message, true);
      }
    },
    // another archive for the next start: the program window asks for its folder and says what will open
    async "settings-archive"(el) {
      try {
        await postJson("/api/app/choose-archive", {});
        say(el, "Выберите папку в открывшемся окне");
      } catch (err) {
        say(el, err.message, true);
      }
    },
    async "settings-act"(el) {
      if (el.dataset.ask && !confirmed(el, el.dataset.ask)) return;
      el.disabled = true;
      const input = el.dataset.input ? page.querySelector(el.dataset.input) : null;
      try {
        const r = await postJson("/api/settings/act", { act: el.dataset.act, value: input ? input.value : "" });
        const out = el.dataset.out ? page.querySelector(el.dataset.out) : null;
        if (out && r.show) { out.hidden = false; out.textContent = r.show; }
        say(el, r.message || "Готово");
        if (r.reload) setTimeout(() => location.reload(), 900);
        else if (comicWatcher && el.dataset.act === "comic_manga_check") comicWatcher.started();
        else if (moveWatcher && el.dataset.act.startsWith("move_")) {
          if (r.id) moveWatcher.started();
          else moveWatcher.poll();
        }
      } catch (err) {
        say(el, err.message, true);
      } finally {
        el.disabled = false;
      }
    },
  });
})();

// (v5.44.64) «Телефон» (webui/pages/phone.py): what Tailscale on this computer is — asked after the page is shown
// (/api/phone/state: the command takes seconds), «Пускать телефон» / «Не пускать телефон», the address and its QR code.
(() => {
  "use strict";
  const LB = window.LocalBooru;
  const panel = document.getElementById("set-phone");
  if (!LB || !panel) return;
  const { actions, postJson, say } = LB;
  const box = panel.querySelector("[data-phone-status]");
  const open = panel.querySelector("[data-phone-open]");
  const onButton = panel.querySelector('[data-action="phone-switch"][data-on="1"]');
  const offButton = panel.querySelector('[data-action="phone-switch"][data-on=""]');
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const link = (href, text) => {
    const a = el("a", "", text);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    return a;
  };

  function render(st) {
    const t = st.tailscale || {};
    box.textContent = "";
    if (!t.installed) {
      box.append(el("p", "tool-warn", "Tailscale на этом компьютере не установлен."));
      const p = el("p", "hint");
      p.append("Скачайте его с ", link("https://tailscale.com/download", "tailscale.com/download"),
        ", установите и войдите тем же аккаунтом, что на телефоне. Потом нажмите «Проверить снова».");
      box.append(p);
    } else if (!t.running) {
      box.append(el("p", "tool-warn", t.words || "Tailscale пока не готов"));
    } else {
      box.append(el("p", "", `Tailscale работает: этот компьютер в вашей сети — ${t.name}, аккаунт ${t.account}.`));
      if (!t.https && !st.on) {
        box.append(el("p", "hint", "При «Пускать телефон» Tailscale может один раз попросить включить HTTPS для " +
          "вашей сети — программа даст ссылку."));
      }
    }
    if (st.on) {
      box.append(el("p", st.serving ? "" : "tool-warn", st.serving ? "Телефон пускается." :
        "Телефон пускается, но Tailscale сейчас не ведёт к программе — когда он заработает, нажмите «Проверить снова»."));
      if (st.renamed) box.append(el("p", "hint", "Имя компьютера в Tailscale поменялось — адрес обновится сам."));
    }
    onButton.hidden = !!st.on || !t.running;
    offButton.hidden = !st.on;
    open.hidden = !(st.on && st.address);
    if (st.on && st.address) {
      const a = open.querySelector("[data-phone-address]");
      a.href = st.address;
      a.textContent = st.address;
      open.querySelector("[data-phone-copy]").dataset.copy = st.address;
      const qr = open.querySelector("[data-phone-qr]");
      qr.hidden = !st.qr;
      if (st.qr) qr.src = "/api/phone/qr.svg?t=" + Date.now();
    }
  }

  async function load() {
    box.textContent = "";
    box.append(el("p", "hint", "Спрашиваю Tailscale…"));
    try {
      const r = await fetch("/api/phone/state", { credentials: "same-origin" });
      render(await r.json());
    } catch (err) {
      box.textContent = "";
      box.append(el("p", "tool-warn", "Программа не ответила: " + err.message));
    }
  }

  actions["phone-check"] = () => load();
  actions["phone-switch"] = async (button) => {
    const on = !!button.dataset.on;
    button.disabled = true;
    say(button, on ? "Говорю Tailscale пускать телефон…" : "Выключаю…");
    try {
      const r = await postJson("/api/phone/switch", { on });
      say(button, r.message || "Готово");
      await load();
    } catch (err) {
      say(button, err.message, true);
      const where = err.data && err.data.link;
      if (where) {
        const p = el("p", "tool-warn");
        p.append("Ссылка: ", link(where, where));
        box.append(p);
      }
    } finally {
      button.disabled = false;
    }
  };
  // asked only when the tab «Телефон» is opened and (v5.44.71) its part «Через Tailscale» is open: the command of
  // Tailscale takes seconds
  const part = panel.querySelector("[data-phone-tailscale]");
  let asked = false;
  const ask = () => {
    if (asked || (part && !part.open)) return;
    asked = true;
    load();
  };
  if (part) part.addEventListener("toggle", ask);
  if ("IntersectionObserver" in window) {
    const seen = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      seen.disconnect();
      ask();
    });
    seen.observe(panel);
  } else {
    ask();
  }
})();
