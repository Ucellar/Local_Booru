// Local Booru page «Настройки»: tabs, one part at a time (a section's anchor #set-… opens its tab); a change in any
// section shows the bar «Сохранить / Отменить изменения» — it saves every changed section (each on its own,
// as the program checks them); «Выбрать…» asks the program window for a folder or a file; actions of a section
// (a copy now, a pairing code, …) are buttons with data-action="settings-act".
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
      out[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    return out;
  }

  // ── tabs ─────────────────────────────────────────────────────────────
  const tabBar = page.querySelector(".settings-tabs");
  const panes = Array.from(page.querySelectorAll(".settings-pane[data-tab]"));
  const tabButtons = tabBar ? Array.from(tabBar.querySelectorAll("[role=tab]")) : [];

  function showTab(id, { focus = false, remember = true } = {}) {
    if (!tabBar || !panes.some((p) => p.dataset.tab === id)) return;
    panes.forEach((p) => { p.hidden = p.dataset.tab !== id && p.dataset.tab !== "other"; });
    tabButtons.forEach((b) => {
      const on = b.dataset.tab === id;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    if (remember) {
      try { localStorage.setItem("lb.settings-tab", id); } catch (_) { /* only this time */ }
      if (history.replaceState) history.replaceState(null, "", "#tab-" + id);
    }
  }

  function tabOf(el) {
    const pane = el && el.closest(".settings-pane[data-tab]");
    return pane ? pane.dataset.tab : "";
  }

  function openFromAddress() {
    const hash = decodeURIComponent(location.hash || "").slice(1);
    if (hash.startsWith("tab-")) { showTab(hash.slice(4), { remember: false }); return true; }
    const target = hash ? document.getElementById(hash) : null;
    if (target && tabOf(target)) {
      showTab(tabOf(target), { remember: false });
      target.scrollIntoView({ block: "start" });
      return true;
    }
    return false;
  }

  if (tabBar && panes.length) {
    tabBar.hidden = false;
    let first = panes[0].dataset.tab;
    try { first = localStorage.getItem("lb.settings-tab") || first; } catch (_) { /* the first tab */ }
    if (!openFromAddress()) showTab(panes.some((p) => p.dataset.tab === first) ? first : panes[0].dataset.tab, { remember: false });
    tabBar.addEventListener("click", (event) => {
      const b = event.target.closest("[role=tab]");
      if (b) showTab(b.dataset.tab);
    });
    tabBar.addEventListener("keydown", (event) => {
      const i = tabButtons.indexOf(document.activeElement);
      if (i < 0) return;
      let next = -1;
      if (event.key === "ArrowRight") next = (i + 1) % tabButtons.length;
      if (event.key === "ArrowLeft") next = (i - 1 + tabButtons.length) % tabButtons.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabButtons.length - 1;
      if (next < 0) return;
      event.preventDefault();
      showTab(tabButtons[next].dataset.tab, { focus: true });
    });
    window.addEventListener("hashchange", openFromAddress);
  }

  // ── one «Сохранить» for the page: the changed sections ─────────────────
  const forms = Array.from(page.querySelectorAll('form[data-form="settings"]'));
  const bar = page.querySelector(".settings-savebar");
  const saved = new Map();              // form → its values as last saved (or as the page came)

  function valuesKey(form) { return JSON.stringify(valuesOf(form)); }
  forms.forEach((form) => saved.set(form, valuesOf(form)));

  function dirtyForms() { return forms.filter((form) => valuesKey(form) !== JSON.stringify(saved.get(form))); }

  let flash = "";                        // what the bar says for a moment after saving
  let flashTimer = 0;

  function refreshBar() {
    const dirty = dirtyForms();
    forms.forEach((form) => form.closest(".settings-block").classList.toggle("changed", dirty.includes(form)));
    tabButtons.forEach((b) => b.classList.toggle("changed", dirty.some((form) => tabOf(form) === b.dataset.tab)));
    if (!bar) return;
    bar.hidden = dirty.length === 0 && !flash;
    bar.classList.toggle("saved", dirty.length === 0 && !!flash);
    bar.querySelector("[data-bar-text]").textContent = dirty.length ? "Есть несохранённые изменения" : flash;
    const names = dirty.map((form) => form.closest(".settings-block").querySelector("h2").textContent);
    bar.querySelector("[data-dirty-list]").textContent = names.length ? ": " + names.join(", ") : "";
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
      if (el.type === "checkbox") el.checked = !!values[el.name];
      else el.value = values[el.name];
    });
  }

  function showError(form, text, field) {
    const box = form.querySelector(".form-error");
    if (box) { box.textContent = text; box.hidden = !text; }
    if (!text) return;
    showTab(tabOf(form));
    const input = field ? form.elements.namedItem(field) : null;
    if (input && input.focus) input.focus();
    else form.closest(".settings-block").scrollIntoView({ block: "center" });
  }

  async function saveAll(el) {
    const dirty = dirtyForms();
    if (!dirty.length) { refreshBar(); return true; }
    const button = bar ? bar.querySelector('[data-action="settings-save-all"]') : null;
    if (button) button.disabled = true;
    let ok = 0;
    let reload = false;                     // this page's own look was changed («Оформление страниц»)
    try {
      for (const form of dirty) {
        showError(form, "");
        if (!form.checkValidity()) {          // a number outside its bounds: the browser says what is wrong
          showTab(tabOf(form));
          form.reportValidity();
          say(el || button, (ok ? `Сохранено разделов: ${ok}; ` : "") + "исправьте отмеченное поле", true);
          return false;
        }
        try {
          const values = valuesOf(form);
          const reply = await postJson("/api/settings/save", { section: form.dataset.section, values });
          reload = reload || !!(reply && reply.reload);
          saved.set(form, values);
          ok += 1;
          form.querySelectorAll('input[type="password"]').forEach((input) => {   // a key is not shown back
            if (input.value) {
              input.value = "";
              input.placeholder = "сохранено — оставьте пустым, чтобы не менять";
              const v = saved.get(form);
              v[input.name] = "";
            }
          });
        } catch (err) {
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
    dirtyForms().forEach((form) => { setValues(form, saved.get(form)); showError(form, ""); });
    refreshBar();
    say(el, "Изменения отменены");
  };

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
