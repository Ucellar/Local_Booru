// Local Booru tool pages: the parser run (progress and journal, polled from the program) and
// actions on many gallery cards at once (sections «Корзина» and «Брак»).
(() => {
  "use strict";

  const LB = window.LocalBooru;
  if (!LB) return;
  const { actions, postJson, say } = LB;
  const root = document.documentElement;

  // A dangerous button asks first: the second press within 4 s does it.
  function confirmed(el, question) {
    if (el.classList.contains("confirming")) {
      clearTimeout(el.confirmTimer);
      el.classList.remove("confirming");
      el.textContent = el.dataset.label;
      return true;
    }
    el.classList.add("confirming");
    el.dataset.label = el.textContent;
    el.textContent = question;
    clearTimeout(el.confirmTimer);
    el.confirmTimer = setTimeout(() => {
      el.classList.remove("confirming");
      el.textContent = el.dataset.label;
    }, 4000);
    return false;
  }
  LB.confirmed = confirmed;              // the page scripts after this one (subs.js) ask the same way

  // ── a long task of a page (core/page_jobs): its journal, polled from the program ──
  const TASK_STATUS = { starting: "запускается", running: "идёт", stopping: "останавливается", done: "готово",
    stopped: "остановлено", failed: "прервано ошибкой" };
  const taskRunning = (status) => ["starting", "running", "stopping"].includes(status);

  // job: the .job box (title, status, journal); url: the task's state address.
  // onState(state, busy) — every answer (to enable buttons); onEnd(state) — a task seen going has ended.
  // Returns {poll, started}: started() after the page has started a task, so its end is seen.
  // ``url`` — the state's address, or (v5.44.74) a function giving it anew for every question (a page that asks only
  // for what changed since its last answer)
  LB.watchTask = (job, url, { onState, onEnd } = {}) => {
    const pre = job.querySelector(".job-log");
    let after = 0;
    let shown = 0;
    let timer = 0;
    let wasRunning = false;
    let generation = 0;            // started() begins a new one: answers to polls sent before it are dropped

    function render(st) {
      const busy = taskRunning(st.status);
      if (onState) onState(st, busy);
      if (!st.id) { job.hidden = true; wasRunning = busy; return; }
      job.hidden = false;
      job.dataset.status = st.status;
      if (st.id !== shown) { shown = st.id; pre.textContent = ""; }
      job.querySelector(".job-title").textContent = st.label || "";
      job.querySelector(".job-status").textContent = TASK_STATUS[st.status] || st.status;
      // only lines not shown yet: a poll from a button and a timed one can carry the same lines
      const fresh = (st.log || []).filter((l) => l.seq > after);
      if (fresh.length) {
        const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
        pre.textContent += (pre.textContent ? "\n" : "") + fresh.map((l) => l.text).join("\n");
        if (stick) pre.scrollTop = pre.scrollHeight;
      }
      after = Math.max(after, st.last_seq || 0);
      if (wasRunning && !busy && onEnd) onEnd(st);
      wasRunning = busy;
    }

    async function poll() {
      clearTimeout(timer);
      const asked = after;
      const mine = generation;
      let delay = 3000;
      try {
        const address = typeof url === "function" ? url() : url;
        const r = await fetch(address + (address.includes("?") ? "&" : "?") + "after=" + asked, { credentials: "same-origin" });
        const st = await r.json();
        // an answer from before started() describes the old state: it would hide the new task's end
        if (mine !== generation) return;
        if (st.id && st.id !== shown && asked !== 0) { after = 0; delay = 0; }   // another task: its journal from the start
        else { render(st); delay = taskRunning(st.status) ? 800 : 3000; }
      } catch (err) {
        if (mine !== generation) return;
        delay = 4000;
      }
      timer = setTimeout(poll, delay);
    }

    poll();
    return { poll, started() { generation += 1; wasRunning = true; after = 0; poll(); } };
  };

  // ── journals and report texts: «Скопировать журнал» under each one; Ctrl+A inside one selects only it ──
  // (a journal scrolls in its own box: selecting it by dragging was hard, and Ctrl+A took the whole page)
  function linesWord(n) {
    const d = n % 10, h = n % 100;
    if (d === 1 && h !== 11) return n + " строка";
    if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return n + " строки";
    return n + " строк";
  }

  function copyTools(pre, label) {
    if (pre.dataset.copyTools) return;
    pre.dataset.copyTools = "1";
    const bar = document.createElement("div");
    bar.className = "copy-tools";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link";
    button.textContent = label;
    const said = document.createElement("span");
    said.className = "copy-said";
    said.setAttribute("role", "status");
    bar.append(button, said);
    pre.after(bar);
    button.addEventListener("click", async () => {
      const text = (pre.textContent || "").replace(/\s+$/, "");
      clearTimeout(said.timer);
      said.classList.remove("error");
      if (!text) {
        said.textContent = "Здесь пока пусто";
      } else if (await LB.copyText(text)) {
        said.textContent = "Скопировано: " + linesWord(text.split("\n").length);
      } else {
        said.textContent = "Не получилось — щёлкните в текст, нажмите Ctrl+A, потом Ctrl+C";
        said.classList.add("error");
      }
      said.timer = setTimeout(() => { said.textContent = ""; said.classList.remove("error"); }, 8000);
    });
    pre.addEventListener("keydown", (event) => {
      // event.code: the A key in any keyboard layout (in the Russian one event.key is «ф»)
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.code !== "KeyA") return;
      event.preventDefault();
      const range = document.createRange();
      range.selectNodeContents(pre);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    });
  }
  document.querySelectorAll(".job-log").forEach((pre) => copyTools(pre, "Скопировать журнал"));
  document.querySelectorAll(".diag-pre").forEach((pre) => {
    if (!pre.hasAttribute("tabindex")) pre.tabIndex = 0;         // Ctrl+A needs it focused
    copyTools(pre, "Скопировать текст");
  });

  // ── a list's finder (v5.37: «Найти подписку», «Найти сайт»): rows without the words are hidden ──
  document.addEventListener("input", (event) => {
    const box = event.target.closest && event.target.closest("[data-row-filter]");
    if (!box) return;
    const table = document.querySelector(box.dataset.rowFilter);
    if (!table) return;
    const words = box.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    table.querySelectorAll("tbody tr").forEach((row) => {
      const text = row.textContent.toLowerCase();
      row.hidden = words.length > 0 && !words.every((word) => text.includes(word));
    });
  });

  // ── many cards at once ─────────────────────────────────────────────────
  const selected = new Set();
  LB.selected = selected;                // the grabber page downloads and hides the chosen cards

  function selectBar() { return document.querySelector(".select-bar"); }

  LB.refreshSelection = () => refreshSelection();   // after a page script changes the selection
  function refreshSelection() {
    const bar = selectBar();
    if (!bar) return;
    const count = bar.querySelector(".select-count");
    if (count) count.textContent = selected.size + " выбрано";
    bar.querySelectorAll("[data-needs-selection]").forEach((b) => { b.disabled = selected.size === 0; });
    document.querySelectorAll(".thumbs .thumb").forEach((card) => {
      const on = selected.has(card.dataset.id);
      card.classList.toggle("selected", on);
      card.setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  function setSelecting(on) {
    root.classList.toggle("selecting", on);
    const bar = selectBar();
    if (!bar) return;
    const button = bar.querySelector('[data-action="select-mode"]');
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = on ? "Готово" : "Выбрать";
    bar.querySelector(".select-tools").hidden = !on;
    if (!on) selected.clear();
    refreshSelection();
  }

  // While choosing, a click on a picture selects it instead of opening it.
  document.addEventListener("click", (event) => {
    if (!root.classList.contains("selecting")) return;
    const card = event.target.closest(".thumbs .thumb");
    if (!card) return;
    event.preventDefault();
    event.stopPropagation();
    const id = card.dataset.id;
    if (selected.has(id)) selected.delete(id); else selected.add(id);
    refreshSelection();
  }, true);

  async function bulk(el, url, body, done) {
    el.disabled = true;
    try {
      const result = await postJson(url, body);
      say(el, done(result));
      setTimeout(() => location.reload(), 900);
    } catch (err) {
      say(el, err.message, true);
      el.disabled = false;
    }
  }

  Object.assign(actions, {
    "select-mode": () => setSelecting(!root.classList.contains("selecting")),

    "select-page"() {
      document.querySelectorAll(".thumbs .thumb").forEach((card) => selected.add(card.dataset.id));
      refreshSelection();
    },

    "select-none"() {
      selected.clear();
      refreshSelection();
    },

    "trash-restore": (el) => bulk(el, "/api/trash/restore", { ids: [...selected] },
      (r) => `Возвращено: ${r.restored}` + (r.merged ? `, слито с живыми копиями: ${r.merged}` : "")),

    "trash-purge"(el) {
      if (!confirmed(el, `Удалить ${selected.size} навсегда? Нажмите ещё раз`)) return;
      bulk(el, "/api/trash/purge", { ids: [...selected] }, (r) => `Удалено навсегда: ${r.removed}`);
    },

    "trash-purge-all"(el) {
      if (!confirmed(el, "Удалить всю корзину навсегда? Нажмите ещё раз")) return;
      bulk(el, "/api/trash/purge", { all: true }, (r) => `Корзина очищена: ${r.removed}`);
    },

    async "trash-restore-all"(el) {
      if (!confirmed(el, `Вернуть ${el.dataset.count} из корзины? Нажмите ещё раз`)) return;
      el.disabled = true;
      try {
        await postJson("/api/trash/restore-all", { tags: el.dataset.tags || "", source: el.dataset.source || "all" });
        say(el, "Возвращаю — журнал выше");
        if (trashWatcher) trashWatcher().started();
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },

    async "trash-restore-stop"(el) {
      el.disabled = true;
      try {
        await postJson("/api/trash/restore-all/stop", {});
      } catch (err) {
        say(el, err.message, true);
      }
      el.disabled = false;
    },

    async "nomatch-retry"(el) {
      el.disabled = true;
      try {
        await postJson("/api/parser/retry", { ids: [...selected] });
        location.href = "/parser";
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },

    async "brak-kind"(el) {
      el.disabled = true;
      try {
        const r = await postJson("/api/nomatch/kind", { id: Number(el.dataset.id), kind: el.dataset.kind });
        say(el, r.message || "Отмечено");
        setTimeout(() => location.reload(), 600);
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },

    // сборки (core/card_sets.py): a chosen tile stands for its whole сборка
    "sets-gather": (el) => bulk(el, "/api/sets/gather", { ids: [...selected].map(Number) },
      (r) => `Собрано в одну сборку: ${r.cards}`),

    "sets-dissolve"(el) {
      if (!confirmed(el, "Разобрать сборки выбранного? Нажмите ещё раз")) return;
      bulk(el, "/api/sets/dissolve", { ids: [...selected].map(Number) }, (r) => `Разобрано сборок: ${r.dissolved}`);
    },

    async "sets-add"(el) {
      const bar = el.closest(".select-bar");
      el.disabled = true;
      try {
        const r = await postJson("/api/sets/gather", { ids: [...selected].map(Number), into: Number(bar.dataset.addTo) });
        say(el, `В сборке теперь ${r.cards}`);
        setTimeout(() => { location.href = bar.dataset.back; }, 600);
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },

    async "nomatch-retry-all"(el) {
      if (!confirmed(el, "Искать снова весь Брак? Нажмите ещё раз")) return;
      el.disabled = true;
      try {
        await postJson("/api/parser/retry", { all: true });
        location.href = "/parser";
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },
  });

  // the gallery opened to choose cards for a сборка (?add_to=…): choosing from the start
  const adding = document.querySelector(".select-bar[data-add-to]");
  if (adding) setSelecting(true);

  // ── the parser page ────────────────────────────────────────────────────
  const tool = document.getElementById("parser");
  if (tool) setupParser(tool);
  const sitesTool = document.getElementById("sites");
  if (sitesTool) setupSites(sitesTool);
  const brak = document.getElementById("brak");
  if (brak) setupBrak(brak);
  const trashBar = document.getElementById("trash-bar");
  let trashWatcher = null;               // () => the watcher of «Вернуть всё» (made on first use)
  if (trashBar) trashWatcher = setupTrash(trashBar);

  // ── the trash: the journal of «Вернуть всё»; at its end the page shows what is left ──
  function setupTrash(bar) {
    const job = bar.querySelector("[data-job]");
    const stop = bar.querySelector('[data-action="trash-restore-stop"]');
    const stateUrl = "/api/trash/restore-all/state";
    let watcher = null;

    function watch() {
      watcher = watcher || LB.watchTask(job, stateUrl, {
        onState(st, busy) {
          stop.hidden = !busy;
          document.querySelectorAll('[data-action="trash-restore-all"], [data-action="trash-purge-all"], '
            + '[data-action="trash-purge"]').forEach((b) => {
            if (busy) b.disabled = true;
            else if (!b.hasAttribute("data-needs-selection")) b.disabled = false;
          });
        },
        onEnd() { setTimeout(() => location.reload(), 2500); },
      });
      return watcher;
    }

    fetch(stateUrl, { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => {      // a going task, or one that ended a moment ago (the page reloaded at its end)
        if (st.id && (taskRunning(st.status) || st.ended_ago < st.show_ended)) watch();
      })
      .catch(() => {});
    return watch;
  }

  // ── a Брак card: «Получить теги по ссылке» (its journal is watched only once there is a task of this card) ──
  function setupBrak(section) {
    const form = section.querySelector('form[data-form="nomatch-url"]');
    const job = section.querySelector("[data-job]");
    const button = form.querySelector('button[type="submit"]');
    const stateUrl = "/api/nomatch/state?card=" + encodeURIComponent(section.dataset.id);
    let watcher = null;

    function watch() {
      watcher = watcher || LB.watchTask(job, stateUrl, {
        onState: (st, busy) => { button.disabled = busy; },
        onEnd(st) {
          if (st.status === "done" && st.found) {
            say(button, "Готово — открываю найденную карточку");
            setTimeout(() => { location.href = "/post/" + st.found; }, 1200);
          } else if (st.status === "failed") {
            say(button, st.error || "Не получилось — см. журнал", true);
          } else {
            say(button, st.status === "stopped" ? "Остановлено" : "Готово");
          }
        },
      });
      return watcher;
    }

    fetch(stateUrl, { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => { if (st.id) watch(); })          // a task of this card is going or has ended: its journal
      .catch(() => {});

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const address = form.elements.namedItem("url").value.trim();
      button.disabled = true;
      try {
        await postJson("/api/nomatch/url", { id: Number(section.dataset.id), url: address });
        say(button, "Спрашиваю сайт…");
        watch().started();
      } catch (err) {
        say(button, err.message, true);
        button.disabled = false;
      }
    });
  }

  // ── the page «Сайты и ключи» ───────────────────────────────────────────
  function setupSites(section) {
    const data = JSON.parse((document.getElementById("sites-data") || {}).textContent || "{}");
    const dialog = section.querySelector(".site-dialog");
    const form = dialog.querySelector('form[data-form="site"]');
    const field = (name) => form.elements.namedItem(name);

    function fill(site) {
      field("id").value = site.id || "";
      field("enabled").checked = site.enabled !== false;
      ["name", "domain", "base_url", "login", "user_id", "login_url", "description"].forEach((k) => {
        field(k).value = site[k] || "";
      });
      field("name").placeholder = site.domain || "";
      field("engine_label").value = site.engine_label || "Свой";
      field("api_key").value = "";
      field("api_key").placeholder = site.has_api_key ? "ключ сохранён — оставьте пустым, чтобы не менять" : "";
      field("api_key_clear").checked = false;
      field("api_key_clear").parentElement.hidden = !site.has_api_key;
      field("cookies").value = "";
      dialog.querySelector('[data-action="site-delete"]').hidden = !site.id;
      dialog.querySelector('[data-action="site-delete"]').textContent = site.custom ? "Удалить сайт" : "Убрать из списка";
      dialog.querySelector("#site-dialog-title").textContent = site.id ? (site.name || site.domain) : "Новый сайт";
      const status = dialog.querySelector(".action-status");
      if (status) status.textContent = "";
    }

    function formData() {
      const out = {};
      ["name", "domain", "base_url", "login", "user_id", "login_url", "description", "engine_label", "api_key"].forEach((k) => {
        out[k] = field(k).value.trim();
      });
      out.enabled = field("enabled").checked;
      out.api_key_clear = field("api_key_clear").checked;
      return out;
    }

    async function checkRow(row) {
      const cell = row.querySelector(".site-check");
      cell.textContent = "проверяю…";
      cell.className = "site-check";
      try {
        const r = await postJson("/api/sites/check", { id: row.dataset.id });
        cell.textContent = (r.site_ok ? "✓ " : "✗ ") + r.message;
        cell.classList.add(r.site_ok ? "good" : "bad");
      } catch (err) {
        cell.textContent = "✗ " + err.message;
        cell.classList.add("bad");
      }
    }

    section.addEventListener("change", async (event) => {
      const box = event.target.closest("[data-site-toggle]");
      if (!box) return;
      const row = box.closest("tr");
      try {
        await postJson("/api/sites/toggle", { id: box.dataset.id, enabled: box.checked });
        row.classList.toggle("off", !box.checked);
      } catch (err) {
        box.checked = !box.checked;
        say(section.querySelector(".tool-actions"), err.message, true);
      }
    });

    Object.assign(actions, {
      "site-edit"(el) {
        fill(data[el.dataset.id] || {});
        dialog.showModal();
      },
      "site-add"() {
        fill({ enabled: true, engine_label: "Свой" });
        dialog.showModal();
        field("domain").focus();
      },
      "site-dialog-close": () => dialog.close(),
      async "site-move"(el) {
        try {
          await postJson("/api/sites/move", { id: el.dataset.id, delta: Number(el.dataset.delta) });
          location.reload();
        } catch (err) { say(el, err.message, true); }
      },
      async "site-delete"(el) {
        if (!confirmed(el, "Точно? Нажмите ещё раз")) return;
        try {
          await postJson("/api/sites/delete", { id: field("id").value });
          location.reload();
        } catch (err) { say(el, err.message, true); }
      },
      async "site-restore"(el) {
        try {
          await postJson("/api/sites/restore");
          location.reload();
        } catch (err) { say(el, err.message, true); }
      },
      async "site-check-all"(el) {
        el.disabled = true;
        const rows = Array.from(section.querySelectorAll(".sites-table tbody tr"))
          .filter((row) => row.querySelector("[data-site-toggle]").checked);
        await Promise.all(rows.map(checkRow));
        el.disabled = false;
        say(el, rows.length ? "Проверено: " + rows.length : "Нет включённых сайтов");
      },
      async "site-cookies"(el) {
        try {
          const r = await postJson("/api/sites/cookies", { domain: field("domain").value, text: field("cookies").value });
          say(el, `Cookies сохранены: ${r.lines} шт.` + (r.cf_clearance ? ", есть cf_clearance" : ", без cf_clearance (для Cloudflare нужен)"));
        } catch (err) { say(el, err.message, true); }
      },
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      try {
        await postJson("/api/sites/row", { id: field("id").value, form: formData() });
        location.reload();
      } catch (err) {
        say(button, err.message, true);
      }
    });

    section.querySelectorAll("form.settings-form").forEach((settingsForm) => {
      settingsForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const body = {};
        Array.from(settingsForm.elements).forEach((el) => {
          if (!el.name) return;
          body[el.name] = el.type === "checkbox" ? el.checked : el.value;
        });
        const button = settingsForm.querySelector('button[type="submit"]');
        try {
          await postJson("/api/sites/" + (settingsForm.dataset.form === "services" ? "services" : "options"), body);
          say(button, "Сохранено");
          const key = settingsForm.querySelector('input[name="saucenao_api_key"]');
          if (key && key.value) { key.value = ""; key.placeholder = "ключ сохранён — оставьте пустым, чтобы не менять"; }
        } catch (err) {
          say(button, err.message, true);
        }
      });
    });
  }

  function setupParser(section) {
    const job = section.querySelector("[data-job]");
    const idle = section.querySelector("[data-idle]");
    const pre = job.querySelector(".job-log");
    const tabs = job.querySelector(".log-tabs");
    const cardsBox = job.querySelector(".flight-cards");
    const flightEmpty = job.querySelector(".flight-empty");
    const flightMore = job.querySelector(".flight-more");
    const recentPart = job.querySelector(".run-recent");
    const recentBox = job.querySelector(".recent-strip");
    const queuesPart = job.querySelector(".run-queues");
    const queuesBox = job.querySelector('.site-queues[data-kind="site"]');
    const reverseBox = job.querySelector('.site-queues[data-kind="reverse"]');
    const reverseTitle = job.querySelector('.queues-group[data-for="reverse"]');
    const filesBox = job.querySelector('.site-queues[data-kind="files"]');
    const filesTitle = job.querySelector('.queues-group[data-for="files"]');
    const viewer = section.querySelector(".parser-viewer");
    const lines = [];                  // [{seq, channel, text}], the last LOG_KEEP
    const LOG_KEEP = 4000;
    const RECENT_SHOWN = 48;
    const channels = new Set();
    let after = 0;
    let shownJob = 0;
    let filter = "all";
    let timer = 0;
    let lastStatus = "";
    let flight = [];                   // files in work, as the last answer gave them
    let recent = [];                   // the last answers, newest first
    let lanes = {};                    // site or service → {status, file, file_id}

    const button = (name) => section.querySelector(`[data-action="${name}"]`);
    const running = (status) => ["starting", "running", "paused", "stopping"].includes(status);
    const STATUS = {
      starting: "запускается", running: "идёт", paused: "на паузе", stopping: "останавливается",
      done: "закончен", failed: "остановлен ошибкой", idle: "", busy: "занят",
    };
    const OUTCOME = {
      tagged: ["найдено", "✓"], nomatch: ["в Брак", "?"], deferred: ["отложен", "‖"],   // text marks, not emoji
      skip: ["пропущен", "–"], error: ["ошибка", "!"],
    };
    const thumbUrl = (id) => `/api/parser/thumb/${shownJob}/${id}`;
    const fileUrl = (id, frame) => `/api/parser/file/${shownJob}/${id}` + (frame ? "?frame=1" : "");

    // «Журнал» remembers whether it is open (only for this viewer: a convenience)
    const logBox = job.querySelector("details[data-remember]");
    if (logBox) {
      try {
        const kept = localStorage.getItem("lb." + logBox.dataset.remember);
        if (kept === "closed") logBox.open = false;
      } catch (_) { /* no storage here: open */ }
      logBox.addEventListener("toggle", () => {
        try { localStorage.setItem("lb." + logBox.dataset.remember, logBox.open ? "open" : "closed"); } catch (_) { /* kept only in this page */ }
      });
    }

    function minutes(sec) {
      if (sec == null) return "";
      if (sec < 60) return sec + " с";
      const m = Math.round(sec / 60);
      return m < 60 ? m + " мин" : Math.floor(m / 60) + " ч " + (m % 60) + " мин";
    }

    function channelLabel(ch) {
      if (ch === "all") return "Всё";
      if (ch === "final") return "Итоги файлов";
      if (ch === "reverse") return "Обратный поиск";
      if (ch === "main") return "Общее";
      return ch.startsWith("site:") ? ch.slice(5) : ch;
    }

    function renderTabs() {
      const wanted = ["all", "final", ...[...channels].filter((c) => c.startsWith("site:")).sort(), "reverse"]
        .filter((c, i, a) => a.indexOf(c) === i && (c === "all" || c === "final" || channels.has(c)));
      if (tabs.childElementCount === wanted.length) return;
      tabs.textContent = "";
      wanted.forEach((ch) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "link" + (ch === filter ? " active" : "");
        b.setAttribute("role", "tab");
        b.setAttribute("aria-selected", ch === filter ? "true" : "false");
        b.dataset.channel = ch;
        b.textContent = channelLabel(ch);
        tabs.appendChild(b);
      });
    }

    tabs.addEventListener("click", (event) => {
      const b = event.target.closest("[data-channel]");
      if (!b) return;
      filter = b.dataset.channel;
      tabs.querySelectorAll("[data-channel]").forEach((x) => {
        const on = x === b;
        x.classList.toggle("active", on);
        x.setAttribute("aria-selected", on ? "true" : "false");
      });
      redrawLog();
    });

    function visible(line) { return filter === "all" || line.channel === filter; }

    function redrawLog() {
      pre.textContent = lines.filter(visible).map((l) => l.text).join("\n");
      pre.scrollTop = pre.scrollHeight;
    }

    function appendLog(fresh) {
      const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
      fresh.forEach((l) => { lines.push(l); channels.add(l.channel); });
      const overflow = lines.length - LOG_KEEP;
      if (overflow > 0) {
        lines.splice(0, overflow);
        redrawLog();
      } else {
        const text = fresh.filter(visible).map((l) => l.text).join("\n");
        if (text) pre.textContent += (pre.textContent ? "\n" : "") + text;
      }
      if (stick) pre.scrollTop = pre.scrollHeight;
      renderTabs();
    }

    // ── what a site or a service is doing with a file ──
    function laneState(status) {
      const s = String(status || "").toLowerCase();
      if (s.startsWith("найдено")) return "found";
      if (s === "нет") return "miss";
      if (s.startsWith("ошибка")) return "error";
      return "busy";
    }

    function laneChip(name, lane) {
      const li = document.createElement("li");
      li.className = "lane lane-" + laneState(lane.status);
      li.append(name + " ");
      const small = document.createElement("small");
      small.textContent = lane.status || "";
      li.appendChild(small);
      return li;
    }

    function lanesOf(id) {
      return Object.keys(lanes).sort().filter((name) => Number(lanes[name].file_id) === Number(id));
    }

    function kindBadge(item) {
      if (item.kind !== "video" && item.kind !== "gif") return null;
      const b = document.createElement("span");
      b.className = "kind-badge";
      b.textContent = item.kind === "video" ? "видео" : "gif";
      return b;
    }

    // ── the pictures in work: one card per file, kept while the file is in work (its picture loads once) ──
    function renderFlight() {
      const ids = new Set(flight.map((f) => String(f.id)));
      cardsBox.querySelectorAll(".flight-card").forEach((card) => { if (!ids.has(card.dataset.id)) card.remove(); });
      flight.forEach((item, index) => {
        let card = cardsBox.querySelector(`.flight-card[data-id="${item.id}"]`);
        if (!card) {
          card = document.createElement("li");
          card.className = "flight-card";
          card.dataset.id = String(item.id);
          const pic = document.createElement("button");
          pic.type = "button";
          pic.className = "flight-pic";
          pic.dataset.action = "pv-show";
          pic.dataset.list = "flight";
          pic.dataset.id = String(item.id);
          pic.setAttribute("aria-label", "Открыть крупно: " + item.name);
          const img = document.createElement("img");
          img.alt = "";
          img.decoding = "async";
          img.src = thumbUrl(item.id);
          pic.appendChild(img);
          const badge = kindBadge(item);
          if (badge) pic.appendChild(badge);
          const name = document.createElement("p");
          name.className = "flight-name";
          name.textContent = item.name;
          name.title = item.name;
          const chips = document.createElement("ul");
          chips.className = "lane-chips";
          card.append(pic, name, chips);
        }
        if (cardsBox.children[index] !== card) cardsBox.insertBefore(card, cardsBox.children[index] || null);
        const chips = card.querySelector(".lane-chips");
        chips.textContent = "";
        lanesOf(item.id).forEach((name) => chips.appendChild(laneChip(name, lanes[name])));
      });
      flightEmpty.hidden = flight.length > 0 || !running(lastStatus);
    }

    // ── the last answers: a strip of small pictures with their result ──
    function renderRecent() {
      recentPart.hidden = recent.length === 0;
      const shown = recent.slice(0, RECENT_SHOWN);
      const ids = new Set(shown.map((r) => String(r.id)));
      recentBox.querySelectorAll(".recent-item").forEach((li) => { if (!ids.has(li.dataset.id)) li.remove(); });
      shown.forEach((item, index) => {
        let li = recentBox.querySelector(`.recent-item[data-id="${item.id}"]`);
        if (!li) {
          li = document.createElement("li");
          li.dataset.id = String(item.id);
          const b = document.createElement("button");
          b.type = "button";
          b.dataset.action = "pv-show";
          b.dataset.list = "recent";
          b.dataset.id = String(item.id);
          const img = document.createElement("img");
          img.alt = "";
          img.loading = "lazy";
          img.decoding = "async";
          img.src = thumbUrl(item.id);
          const mark = document.createElement("span");
          mark.className = "st-badge";
          mark.setAttribute("aria-hidden", "true");
          b.append(img, mark);
          li.appendChild(b);
        }
        const [word, glyph] = OUTCOME[item.status] || [item.status, "•"];
        li.className = "recent-item st-" + item.status;
        li.querySelector(".st-badge").textContent = glyph;
        const what = word + (item.status === "tagged" && item.tags ? `, тегов: ${item.tags}` : "");
        li.querySelector("button").title = item.name + " — " + what;
        li.querySelector("button").setAttribute("aria-label", item.name + " — " + what + ". Открыть крупно");
        if (recentBox.children[index] !== li) recentBox.insertBefore(li, recentBox.children[index] || null);
      });
    }

    // ── a site switched off for the run: why, and «Проверить снова» once the person has fixed it ──
    const alertBox = job.querySelector(".run-alert");
    let alertShown = "";
    function renderAlert(list, status) {
      const off = list.filter((q) => q.how === "выключен");
      const live = status === "running" || status === "paused";
      const key = JSON.stringify(off.map((q) => [q.host, q.reason, q.again])) + live;
      if (key === alertShown) return;           // the same: not drawn again (a reader keeps its place)
      alertShown = key;
      alertBox.hidden = off.length === 0;
      alertBox.textContent = "";
      off.forEach((q) => {
        const row = document.createElement("p");
        row.className = "alert-row";
        const what = document.createElement("span");
        what.className = "alert-what";
        const name = document.createElement("b");
        name.textContent = q.site;
        what.append(name, " отключён до конца запуска" + (q.reason ? ": " + q.reason : ""));
        row.appendChild(what);
        if (q.kind === "reverse") {             // a service: the files go on to the next ones; back on next run
          const note = document.createElement("span");
          note.className = "alert-again";
          note.textContent = "файлы ищутся следующими сервисами; в следующем запуске он снова спрашивается";
          row.appendChild(note);
          alertBox.appendChild(row);
          return;
        }
        if (live) {
          const again = document.createElement("button");
          again.type = "button";
          again.dataset.action = "parser-site-again";
          again.dataset.host = q.host;
          again.textContent = "Проверить снова";
          again.disabled = q.again === "проверяю…";
          row.appendChild(again);
        }
        const note = document.createElement("span");
        note.className = "alert-again";
        note.textContent = q.again || (live ? "файлы, прошедшие без него, проверит «Добор новых сайтов» в следующем запуске" : "");
        row.appendChild(note);
        alertBox.appendChild(row);
      });
    }

    // ── the sites' own queues: how each asks, how many files wait for it, what it asks now ──
    // the files' own stages: reading, writing into the library, the checks after the sites
    const FILE_STAGES = {
      read: (q) => `ждут чтения ${q.waiting} · прочитано ${q.answered}`,
      writes: (q) => `ждут записи ${q.waiting} · записано ${q.answered}`,
      after: (q) => `ждут проверки ${q.waiting} · проверено ${q.answered}`,
    };

    function perTask(seconds) {
      return (seconds < 0.1 ? Math.round(seconds * 1000) + " мс" : seconds.toFixed(1) + " с") + " на файл";
    }

    function queueState(q) {
      if (q.how === "выключен") return "off";
      if (q.how === "пауза") return "paused";
      if (q.kind === "files") return (q.stage === "read" ? q.reading : q.waiting) > 0 ? "busy" : "idle";
      return q.waiting > 0 || q.file ? "busy" : "idle";
    }

    function renderQueues(all) {
      queuesPart.hidden = all.length === 0;
      const reverse = all.filter((q) => q.kind === "reverse");
      const files = all.filter((q) => q.kind === "files");    // the files' own stage: read, then written
      reverseBox.hidden = reverseTitle.hidden = reverse.length === 0;
      filesBox.hidden = filesTitle.hidden = files.length === 0;
      fillQueues(filesBox, files);
      fillQueues(queuesBox, all.filter((q) => q.kind !== "reverse" && q.kind !== "files"));
      fillQueues(reverseBox, reverse);
    }

    function fillQueues(box, list) {
      const names = new Set(list.map((q) => q.site));
      box.querySelectorAll(".site-queue").forEach((li) => { if (!names.has(li.dataset.site)) li.remove(); });
      list.forEach((q, index) => {
        let li = box.querySelector(`.site-queue[data-site="${CSS.escape(q.site)}"]`);
        if (!li) {
          li = document.createElement("li");
          li.dataset.site = q.site;
          const name = document.createElement("b");
          name.className = "q-site";
          name.textContent = q.site;
          const how = document.createElement("span");
          how.className = "q-how";
          const numbers = document.createElement("span");
          numbers.className = "q-numbers";
          const now = document.createElement("span");
          now.className = "q-now";
          li.append(name, how, numbers, now);
        }
        li.className = "site-queue q-" + queueState(q);
        li.querySelector(".q-how").textContent = q.how;
        let numbers = q.kind !== "files" ? `ждут ${q.waiting} · ответил ${q.answered}`
          : FILE_STAGES[q.stage === "writes" || q.stage === "after" ? q.stage : "read"](q);
        // its own pace and how long what waits for it takes at that pace
        if (q.per_minute > 0) numbers += ` · ${q.per_minute < 10 ? q.per_minute.toFixed(1) : Math.round(q.per_minute)} в мин`;
        if (q.kind === "files" && q.stage !== "read" && q.average > 0) numbers += " · " + perTask(q.average);
        if (q.eta != null && (q.waiting > 0 || (q.kind === "files" && q.stage !== "read"))) numbers += " · ~" + minutes(q.eta);
        li.querySelector(".q-numbers").textContent = numbers;
        const now = li.querySelector(".q-now");
        if (q.kind === "files") now.textContent = q.stage === "read" && q.reading > 0 ? "читаются сейчас: " + q.reading : "";
        else now.textContent = q.file ? (q.kind === "reverse" ? "ищет: " : "спрашивает: ") + q.file
          : (q.how === "пауза" ? "ждёт конца паузы" : "");
        now.title = q.file || "";
        if (box.children[index] !== li) box.insertBefore(li, box.children[index] || null);
      });
    }

    // ── progress: one bar split by answers, the rest is still to do ──
    function renderBar(state) {
      const bar = job.querySelector(".run-bar");
      const total = Math.max(1, state.total || 0);
      const counts = state.counts || {};
      ["tagged", "nomatch", "deferred", "skip", "error"].forEach((key) => {
        const seg = bar.querySelector(".seg-" + key);
        seg.style.width = Math.min(100, (100 * (counts[key] || 0)) / total) + "%";
      });
      const done = state.done || 0;
      const percent = state.total ? Math.floor((100 * done) / state.total) : 0;
      bar.querySelector(".run-percent").textContent = state.total ? percent + "%" : "";
      bar.setAttribute("aria-valuemax", String(total));
      bar.setAttribute("aria-valuenow", String(done));
      bar.setAttribute("aria-valuetext", state.total ? `${done} из ${state.total}` : "файлы ещё считаются");
    }

    function render(state) {
      const status = state.status || "idle";
      button("parser-start").disabled = running(status) || status === "busy";
      button("parser-pause").disabled = !(status === "running" || status === "paused");
      button("parser-pause").textContent = status === "paused" ? "Продолжить" : "Пауза";
      button("parser-pause").dataset.paused = status === "paused" ? "1" : "";
      button("parser-stop").disabled = !(status === "running" || status === "paused");
      if (status === "busy") {
        say(button("parser-start"), "Парсер запущен из другого места (" + (state.owner || "?") + ")");
      }
      if (!state.id) { job.hidden = true; idle.hidden = false; return; }
      job.hidden = false;
      idle.hidden = true;
      if (state.id !== shownJob) {            // another run: its journal and pictures afresh
        shownJob = state.id;
        lines.length = 0;
        channels.clear();
        pre.textContent = "";
        tabs.textContent = "";
        cardsBox.textContent = "";
        recentBox.textContent = "";
        queuesBox.textContent = "";
        reverseBox.textContent = "";
        filesBox.textContent = "";
      }
      job.querySelector(".job-title").textContent = state.label || "Парсер";
      job.querySelector(".job-status").textContent = STATUS[status] || status;
      job.dataset.status = status;
      renderBar(state);
      const numbers = [];
      if (state.total) numbers.push(`${state.done} из ${state.total}`);
      numbers.push(minutes(state.elapsed) + (running(status) ? " идёт" : ""));
      if (state.eta != null && running(status)) {
        numbers.push("осталось ~" + minutes(state.eta) + (state.eta_by ? " (дольше всех — " + state.eta_by + ")" : ""));
      } else if (running(status) && state.total > state.done) {
        // not known yet: the stage that has work and no pace is named
        numbers.push("сколько осталось — станет ясно, когда " + (state.eta_by ? "разгонится: " + state.eta_by
          : "очереди разгонятся"));
      }
      job.querySelector(".job-numbers").textContent = numbers.join(" · ");
      Object.entries(state.counts || {}).forEach(([k, v]) => {
        const dd = job.querySelector(`[data-count="${k}"]`);
        if (dd) dd.textContent = String(v);
      });
      job.querySelector(".job-links").hidden = running(status);
      lastStatus = status;
      lanes = state.activity || {};
      flight = state.files || [];
      recent = state.recent || [];
      renderFlight();
      const more = Number(state.files_more || 0);
      flightMore.hidden = more <= 0;
      flightMore.textContent = more > 0 ? `…и ещё ${more} — ждут ответа сайтов (их очереди — выше)` : "";
      renderRecent();
      renderQueues(state.queues || []);
      renderAlert(state.queues || [], status);
      if (viewer.open && pv.list === "flight") viewerLanes();
      // only lines not shown yet: a poll from a button and a timed one can carry the same lines
      const fresh = (state.log || []).filter((l) => l.seq > after);
      if (fresh.length) appendLog(fresh);
      after = Math.max(after, state.last_seq || 0);
    }

    let wasRunning = false;
    async function poll() {
      clearTimeout(timer);
      const asked = after;
      try {
        const response = await fetch("/api/parser/state?after=" + asked, { credentials: "same-origin" });
        const state = await response.json();
        if (state.id && state.id !== shownJob && asked !== 0) {
          after = 0;                        // a new run since the last look: its journal from the start
          timer = setTimeout(poll, 0);
          return;
        }
        render(state);
        const now = running(state.status);
        if (wasRunning && !now && state.id) {
          say(button("parser-start"), state.status === "failed" ? "Парсер остановлен ошибкой — см. журнал" : "Готово");
        }
        wasRunning = now;
        timer = setTimeout(poll, now ? 700 : 4000);
      } catch (err) {
        timer = setTimeout(poll, 4000);
      }
    }

    // ── the viewer: a picture of the run large, its sites or its answer, the file in its program ──
    const pv = { list: "flight", items: [], index: 0 };

    function viewerItem() { return pv.items[pv.index]; }

    function showInViewer() {
      const item = viewerItem();
      if (!item) return;
      const img = viewer.querySelector(".pv-media img");
      const video = viewer.querySelector(".pv-media video");
      if (item.kind === "video") {
        img.hidden = true;
        img.removeAttribute("src");
        video.hidden = false;
        video.poster = fileUrl(item.id, true);
        video.src = fileUrl(item.id);
      } else {
        video.pause();
        video.removeAttribute("src");
        video.load();
        video.hidden = true;
        img.hidden = false;
        img.src = fileUrl(item.id);
      }
      viewer.querySelector("#pv-title").textContent = item.name;
      const what = viewer.querySelector(".pv-what");
      const chips = viewer.querySelector(".pv-chips");
      chips.textContent = "";
      const card = viewer.querySelector("[data-pv-card]");
      card.hidden = true;
      if (pv.list === "flight") {
        viewerLanes();
      } else {
        const [word] = OUTCOME[item.status] || [item.status];
        let text = word[0].toUpperCase() + word.slice(1);
        if (item.status === "tagged") text += (item.tags ? `: тегов ${item.tags}` : "") + (item.reason ? ` (${item.reason})` : "");
        else if (item.reason && item.status !== "nomatch") text += " — " + item.reason;
        what.textContent = text;
        if (item.md5 && (item.status === "tagged" || item.status === "nomatch")) {
          const params = new URLSearchParams({ tags: "md5:" + item.md5 });
          if (item.status === "nomatch") params.set("section", "no_match");
          card.href = "/posts?" + params.toString();
          card.hidden = false;
        }
      }
      const status = viewer.querySelector(".action-status");
      if (status) status.textContent = "";
      viewer.querySelector(".pv-prev").disabled = pv.index <= 0;
      viewer.querySelector(".pv-next").disabled = pv.index >= pv.items.length - 1;
    }

    // the sites asking about the picture shown, as the last answer says (kept fresh while the viewer is open)
    function viewerLanes() {
      const item = viewerItem();
      if (!item) return;
      const chips = viewer.querySelector(".pv-chips");
      const names = lanesOf(item.id);
      const still = flight.some((f) => String(f.id) === String(item.id));
      viewer.querySelector(".pv-what").textContent = !still ? "Готово — ответ в «Готово недавно»."
        : (names.length ? "Сейчас его ищут:" : "В работе — сайты ещё не ответили.");
      chips.textContent = "";
      if (still) names.forEach((name) => chips.appendChild(laneChip(name, lanes[name])));
    }

    function step(delta) {
      const next = pv.index + delta;
      if (next < 0 || next >= pv.items.length) return;
      pv.index = next;
      showInViewer();
    }

    viewer.addEventListener("close", () => {
      const video = viewer.querySelector(".pv-media video");
      video.pause();
      video.removeAttribute("src");           // the file is let go (the program may need it)
      video.load();
      viewer.querySelector(".pv-media img").removeAttribute("src");
    });
    viewer.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
      if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
    });
    viewer.addEventListener("click", (event) => {            // a click on the dimmed page around it closes it
      if (event.target === viewer) viewer.close();
    });

    async function openFile(el, reveal) {
      const item = viewerItem();
      if (!item) return;
      try {
        await postJson("/api/parser/open", { job: shownJob, file: item.id, reveal: !!reveal });
        say(el, reveal ? "Папка открыта" : "Открыто");
      } catch (err) {
        say(el, err.message, true);
      }
    }

    Object.assign(actions, {
      "pv-show"(el) {
        pv.list = el.dataset.list;
        pv.items = (pv.list === "flight" ? flight : recent.slice(0, RECENT_SHOWN)).slice();
        pv.index = Math.max(0, pv.items.findIndex((x) => String(x.id) === el.dataset.id));
        showInViewer();
        if (!viewer.open) viewer.showModal();
      },
      "pv-prev": () => step(-1),
      "pv-next": () => step(1),
      "pv-close": () => viewer.close(),
      "pv-open": (el) => openFile(el, false),
      "pv-reveal": (el) => openFile(el, true),
      async "parser-start"(el) {
        el.disabled = true;
        const recheck = section.querySelector('input[name="recheck"]');
        const only = section.querySelector('input[name="recheck_only"]');
        try {
          await postJson("/api/parser/start", { recheck_new_sites: !!(recheck && recheck.checked),
            recheck_nomatch_only: !!(only && only.checked) });
          say(el, "Запущено");
          after = 0;
          wasRunning = true;
          poll();
        } catch (err) {
          say(el, err.message, true);
          el.disabled = false;
        }
      },
      async "parser-pause"(el) {
        try {
          await postJson("/api/parser/" + (el.dataset.paused ? "resume" : "pause"));
          poll();
        } catch (err) {
          say(el, err.message, true);
        }
      },
      async "parser-stop"(el) {
        if (!confirmed(el, "Остановить? Нажмите ещё раз")) return;
        try {
          await postJson("/api/parser/stop");
          poll();
        } catch (err) {
          say(el, err.message, true);
        }
      },
      async "parser-site-again"(el) {
        el.disabled = true;
        try {
          await postJson("/api/parser/site-again", { host: el.dataset.host });
          poll();
        } catch (err) {
          const note = el.parentElement.querySelector(".alert-again");
          if (note) note.textContent = err.message;
          el.disabled = false;
        }
      },
      async "parser-choose-folder"(el) {
        try {
          await LB.appRequest("choose-parser-folder");
          say(el, "Выберите папку в открывшемся окне");
        } catch (err) {
          say(el, err.message, true);
        }
      },
    });

    // «Добор только для Брака» means something only with «Добор новых сайтов»
    const recheckBox = section.querySelector('input[name="recheck"]');
    const onlyBox = section.querySelector('input[name="recheck_only"]');
    if (recheckBox && onlyBox) {
      recheckBox.addEventListener("change", () => { onlyBox.disabled = !recheckBox.checked; });
    }

    const form = section.querySelector('[data-form="parser-folder"]');
    if (form) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const input = form.querySelector('input[name="path"]');
        try {
          await postJson("/api/parser/folder", { path: input.value });
          location.reload();
        } catch (err) {
          say(form.querySelector("button"), err.message, true);
        }
      });
    }
    poll();
  }
})();
