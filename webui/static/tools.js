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
  // (v5.44.118) a button asking its second press forgets the question (the choice it asked about changed)
  function unconfirm(el) {
    if (!el.classList.contains("confirming")) return;
    clearTimeout(el.confirmTimer);
    el.classList.remove("confirming");
    el.textContent = el.dataset.label;
  }

  function refreshSelection() {
    const bar = selectBar();
    if (!bar) return;
    const count = bar.querySelector(".select-count");
    if (count) count.textContent = selected.size + " выбрано";
    bar.querySelectorAll("[data-needs-selection]").forEach((b) => { b.disabled = selected.size === 0; });
    // (v5.44.118) «В корзину выбранные (N)»
    bar.querySelectorAll("[data-count-label]").forEach((b) => {
      unconfirm(b);
      b.textContent = b.dataset.countLabel + (selected.size ? " (" + selected.size + ")" : "");
    });
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

    // (v5.44.118) «В корзину выбранные (N)»: a tile of a сборка is all its files (data-files) — the question says how
    // many go; the second press within 4 s moves them (a page task: its journal under the bar)
    async "trash-move"(el) {
      const tiles = [...selected];
      if (!tiles.length) return;
      const files = tiles.reduce((sum, id) => {
        const card = document.querySelector(`.thumbs .thumb[data-id="${CSS.escape(id)}"]`);
        return sum + Math.max(1, Number((card && card.dataset.files) || 1));
      }, 0);
      const question = tiles.length === 1
        ? (files > 1 ? `Точно в корзину выбранную сборку (${filesWord(files)})? Нажмите ещё раз`
          : "Точно в корзину выбранную? Нажмите ещё раз")
        : `Точно в корзину ${tiles.length} выбранных` + (files > tiles.length ? ` (${filesWord(files)})` : "")
          + "? Нажмите ещё раз";
      if (!confirmed(el, question)) return;
      await startMove(el, { ids: tiles.map(Number), section: el.dataset.section || "all" });
    },

    // «В корзину все найденные (N)» — the cards of the search in the address (without a search there is no such
    // button: the whole library never goes by one press)
    async "trash-move-all"(el) {
      if (!confirmed(el, el.dataset.ask || "Точно в корзину все найденные? Нажмите ещё раз")) return;
      await startMove(el, { all: true, tags: el.dataset.tags || "", source: el.dataset.source || "all",
        section: el.dataset.section || "all" });
    },

    async "trash-move-stop"(el) {
      el.disabled = true;
      try {
        await postJson("/api/trash/move/stop", {});
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

    // (v5.44.116) the whole Брак — or, with a search in the address, exactly the cards found (gallery.py: data-tags,
    // data-source); how many went to the parser is said before «Парсер» opens, and that the rest wait, if some do
    async "nomatch-retry-all"(el) {
      const narrowed = !!el.dataset.tags || (el.dataset.source || "all") !== "all";
      if (!confirmed(el, narrowed ? `Искать снова ${el.dataset.count}? Нажмите ещё раз`
        : "Искать снова весь Брак? Нажмите ещё раз")) return;
      el.disabled = true;
      try {
        const r = await postJson("/api/parser/retry", { all: true, tags: el.dataset.tags || "",
          source: el.dataset.source || "all" });
        say(el, r.message || "Ищу снова");
        setTimeout(() => { location.href = "/parser"; }, r.left ? 6000 : 1500);
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },
  });

  // the gallery opened to choose cards for a сборка (?add_to=…): choosing from the start
  const adding = document.querySelector(".select-bar[data-add-to]");
  if (adding) setSelecting(true);

  // ── (v5.44.161) the frame of «Настройки» (its own page and the pages of «Проверка и починка») ─────────────
  // The list of the sections stays in sight while the page scrolls: below the header in the looks whose header
  // stays at the top (--sx-top; a block opened by its address stops below it too).
  const settingsFrame = document.querySelector(".sx");
  if (settingsFrame) {
    const place = () => {
      const header = document.querySelector("header.top");
      let top = 8;
      if (header) {
        const style = window.getComputedStyle(header);
        const box = header.getBoundingClientRect();
        const stays = style.position === "sticky" || style.position === "fixed";
        const across = box.width >= window.innerWidth * 0.6 && box.height < window.innerHeight * 0.4 && box.top <= 1;
        if (stays && across) top = Math.round(box.height) + 8;
      }
      settingsFrame.style.setProperty("--sx-top", top + "px");
    };
    place();
    window.addEventListener("resize", place);
  }

  // ── the parser page ────────────────────────────────────────────────────
  const tool = document.getElementById("parser");
  if (tool) setupParser(tool);
  const mangaBox = document.getElementById("parser-manga");
  if (mangaBox) setupParserManga(mangaBox);
  const sitesTool = document.getElementById("sites");
  if (sitesTool) setupSites(sitesTool);
  const brak = document.getElementById("brak");
  if (brak) setupBrak(brak);
  const trashBar = document.getElementById("trash-bar");
  let trashWatcher = null;               // () => the watcher of «Вернуть всё» (made on first use)
  if (trashBar) trashWatcher = setupTrash(trashBar);
  const moveBox = document.getElementById("trash-move");
  let moveWatcher = null;                // (v5.44.118) () => the watcher of «В корзину…» (made on first use)
  if (moveBox) moveWatcher = setupMove(moveBox);

  function filesWord(n) {
    const d = n % 10, h = n % 100;
    if (d === 1 && h !== 11) return n + " файл";
    if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return n + " файла";
    return n + " файлов";
  }

  async function startMove(el, body) {
    el.disabled = true;
    try {
      await postJson("/api/trash/move", body);
      say(el, "Переношу в корзину — журнал ниже");
      if (moveWatcher) moveWatcher().started();
    } catch (err) {
      say(el, err.message, true);
      el.disabled = !el.hasAttribute("data-needs-selection") ? false : selected.size === 0;
    }
  }

  // ── (v5.44.118) the gallery: the journal of «В корзину выбранные / все найденные»; at its end the page shows what
  // is left (the cards gone into the trash are no longer in it) ──
  function setupMove(box) {
    const job = box.querySelector("[data-job]");
    const stop = box.querySelector('[data-action="trash-move-stop"]');
    const stateUrl = "/api/trash/move/state";
    let watcher = null;

    function watch() {
      watcher = watcher || LB.watchTask(job, stateUrl, {
        onState(st, busy) {
          stop.hidden = !busy;
          document.querySelectorAll('[data-action="trash-move"], [data-action="trash-move-all"]').forEach((b) => {
            if (busy) b.disabled = true;
            else b.disabled = b.hasAttribute("data-needs-selection") && selected.size === 0;
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
    // (v5.44.161) «Обратный поиск» and «Парсер» are rows of «Настройки» now: saved by its one «Сохранить»
    // (settings.js; their forms say where — data-save)
  }

  // (v5.44.125) «Манга в исходной папке»: what the folders that look like a manga turned out to be; the server draws
  // the block (webui/pages/parser_manga.py), the page asks it again — often while the parser runs (its manga are
  // checked beside the files), seldom else; the filter, «Показать все» and the opened сборки stay as they were
  function setupParserManga(box) {
    const inner = box.querySelector(".pm-body");
    let wanted = "all";
    let every = false;
    let timer = 0;
    let asking = false;

    function opened() {
      return new Set(Array.from(inner.querySelectorAll("details.pm-open[open]")).map((d) => d.dataset.key));
    }

    // (v5.44.160) the number on the tab «Манга из папки» of the page «Парсер»: the folders that look like a manga
    function counted(counts) {
      const tab = document.querySelector('[data-px-n="manga"]');
      if (!tab || !counts) return;
      const n = Number(counts.total) || 0;
      tab.textContent = String(n);
      tab.hidden = n === 0;
    }

    async function load() {
      if (asking) return;
      asking = true;
      clearTimeout(timer);
      try {
        const url = `${box.dataset.pmUrl}?state=${encodeURIComponent(wanted)}${every ? "&all=1" : ""}`;
        const response = await fetch(url, { headers: { Accept: "application/json" } });
        const data = await response.json();
        if (data && data.ok && typeof data.html === "string") {
          const keep = opened();
          const why = inner.querySelector("details.mx-why");
          const whyOpen = !!(why && why.open);           // «Как программа решает…» being read stays open
          inner.innerHTML = data.html;
          inner.querySelectorAll("details.pm-open").forEach((d) => { if (keep.has(d.dataset.key)) d.open = true; });
          const whyNow = inner.querySelector("details.mx-why");
          if (whyNow && whyOpen) whyNow.open = true;
          box.dataset.live = data.live ? "1" : "";
          counted(data.counts);
        }
      } catch (_) {
        /* the program busy for a moment: the next time */
      } finally {
        asking = false;
        timer = setTimeout(load, box.dataset.live ? 4000 : 30000);
      }
    }

    // (v5.44.164) what a row's button said — in the row itself (the page's first status line is at its top, out of
    // sight from a row far below: LB's «say» took that one); no row named — the block's own line
    function told(key, text, isError) {
      const row = key ? Array.from(inner.querySelectorAll(".pm-row")).find((r) => r.dataset.key === key) : null;
      const tools = row ? row.querySelector(".pm-tools") : null;
      let status = tools ? tools.querySelector(".action-status") : inner.querySelector(".action-status");
      if (tools && !status) {
        status = document.createElement("span");
        status.className = "action-status";
        status.setAttribute("role", "status");
        tools.appendChild(status);
      }
      if (!status) { say(null, text, isError); return; }
      status.textContent = text;
      status.style.color = isError ? "var(--danger)" : "";
    }

    box.addEventListener("click", async (event) => {
      const chip = event.target.closest("[data-pm-filter]");
      if (chip) {
        wanted = chip.dataset.pmFilter;
        every = false;
        load();
        return;
      }
      const el = event.target.closest("[data-action]");
      if (!el || !box.contains(el)) return;
      const act = el.dataset.action;
      if (act === "pm-all") {
        every = true;
        load();
      } else if (act === "pm-not" || act === "pm-is") {
        event.preventDefault();
        el.disabled = true;
        try {
          await postJson(`/api/parser/manga/${act === "pm-not" ? "not" : "is"}`, { key: el.dataset.key });
          load();
        } catch (err) {
          told(el.dataset.key, err.message, true);
          el.disabled = false;
        }
      } else if (act === "pm-now") {
        // (v5.44.164) «Скачать сейчас» of a manga of the loose pictures: its download waited for the person to leave
        event.preventDefault();
        el.disabled = true;
        const key = el.dataset.key;
        try {
          const done = await postJson("/api/parser/manga/now", { key });
          await load();
          told(key, (done && done.message) || "Скачивание в очереди");
        } catch (err) {
          told(key, err.message, true);
          el.disabled = false;
        }
      }
    });
    box.addEventListener("change", async (event) => {
      const sw = event.target.closest("[data-pm-switch]");
      if (!sw) return;
      try {
        await postJson("/api/parser/manga/switch", { name: sw.dataset.pmSwitch, value: sw.checked });
        told("", "Сохранено");
      } catch (err) {
        sw.checked = !sw.checked;
        told("", err.message, true);
      }
    });
    inner.querySelectorAll("details.pm-open").forEach((d, n) => { if (n === 0) d.open = true; });
    timer = setTimeout(load, box.dataset.live ? 4000 : 30000);
  }

  // (v5.44.160; the mockup A — the owner: «парсер выглядит пиздец толи непонятно толи перегруженно») «Парсер»: on top
  // — what is going on (the state, how much is done and left, who holds the run); the rest behind tabs: the files in
  // work and the last answers («Сейчас» — «Итог» when nothing runs), the table of the queues («Сайты»), the manga of
  // the folder, the journal, the parameters.  The tab shown is in the address (#sites, #manga, #log, #params).
  function setupParser(section) {
    const pre = section.querySelector(".job-log");
    const tabs = section.querySelector(".log-tabs");
    const noLog = section.querySelector("[data-px-nolog]");
    const idle = section.querySelector("[data-idle]");
    const lastPart = section.querySelector("[data-px-last]");
    const flightPart = section.querySelector(".run-flight");
    const cardsBox = section.querySelector(".flight-cards");
    const flightEmpty = section.querySelector(".flight-empty");
    const flightMore = section.querySelector(".flight-more");
    const recentPart = section.querySelector(".run-recent");
    const recentBox = section.querySelector(".recent-strip");
    const queuesPart = section.querySelector("[data-px-queues]");
    const willPart = section.querySelector("[data-px-will]");
    const queueBoxes = {
      files: section.querySelector('.site-queues[data-kind="files"]'),
      site: section.querySelector('.site-queues[data-kind="site"]'),
      reverse: section.querySelector('.site-queues[data-kind="reverse"]'),
    };
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
    let services = new Set();          // the reverse-search services of the run (their «нет» is not a site's)

    const button = (name) => section.querySelector(`[data-action="${name}"]`);
    const running = (status) => ["starting", "running", "paused", "stopping"].includes(status);
    const STATUS = {
      starting: "Запускается", running: "Идёт", paused: "Пауза", stopping: "Останавливается",
      done: "Закончен", failed: "Остановлен ошибкой", idle: "Не запущен", busy: "Занят",
    };
    const OUTCOME = {
      tagged: "найдено", nomatch: "в Брак", deferred: "отложен", skip: "пропущен", error: "ошибка",
    };
    const thumbUrl = (id) => `/api/parser/thumb/${shownJob}/${id}`;
    const fileUrl = (id, frame) => `/api/parser/file/${shownJob}/${id}` + (frame ? "?frame=1" : "");

    // ── words ──
    const num = (n) => String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    function plural(n, one, few, many) {
      const d = Math.abs(n) % 10, h = Math.abs(n) % 100;
      if (d === 1 && h !== 11) return one;
      return d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
    }
    const tagsWord = (n) => num(n) + " " + plural(n, "тег", "тега", "тегов");
    const sitesWord = (n) => n + " " + plural(n, "сайт", "сайта", "сайтов");

    function minutes(sec) {
      if (sec == null) return "";
      if (sec < 60) return sec + " с";
      const m = Math.round(sec / 60);
      return m < 60 ? m + " мин" : Math.floor(m / 60) + " ч " + (m % 60) + " мин";
    }

    const two = (n) => String(n).padStart(2, "0");
    const clock = (d) => two(d.getHours()) + ":" + two(d.getMinutes());
    function dayWord(d) {
      const midnight = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
      const days = Math.round((midnight(new Date()) - midnight(d)) / 86400000);
      return days <= 0 ? "сегодня" : days === 1 ? "вчера" : d.getDate() + "." + two(d.getMonth() + 1);
    }
    // «вчера в 23:10»; «вчера, 23:10 — 06:22» (webui/pages/tools.py says the same: _ago, _span)
    const ago = (stamp) => { const d = new Date(stamp * 1000); return dayWord(d) + " в " + clock(d); };
    function span(started, finished) {
      const a = new Date(started * 1000), b = new Date(finished * 1000);
      const same = dayWord(a) === dayWord(b);
      return dayWord(a) + ", " + clock(a) + " — " + (same ? "" : dayWord(b) + ", ") + clock(b);
    }

    // ── the tabs ──
    const TABS = ["now", "sites", "manga", "log", "params"];
    const tabButtons = Array.from(section.querySelectorAll("[data-px-tab]"));
    function showTab(name) {
      const tab = TABS.includes(name) ? name : "now";
      tabButtons.forEach((b) => {
        const on = b.dataset.pxTab === tab;
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
      });
      section.querySelectorAll("[data-px-pane]").forEach((pane) => { pane.hidden = pane.dataset.pxPane !== tab; });
      section.dataset.tab = tab;
      if (tab === "log") pre.scrollTop = pre.scrollHeight;
      return tab;
    }
    function tabOfAddress() {
      let hash = "";
      try { hash = decodeURIComponent(location.hash.slice(1)); } catch (_) { hash = ""; }
      if (hash === "parser-manga" || hash.startsWith("pm-")) return "manga";       // the address of the block before
      return TABS.includes(hash) ? hash : "now";
    }
    function openTab(name) {                 // by the person: the address says it (a page opened anew shows the same)
      const tab = showTab(name);
      try {
        history.replaceState(null, "", tab === "now" ? location.pathname + location.search : "#" + tab);
      } catch (_) { /* the address stays: the tab is shown */ }
    }
    section.querySelector(".px-tabs").addEventListener("click", (event) => {
      const b = event.target.closest("[data-px-tab]");
      if (b) openTab(b.dataset.pxTab);
    });
    section.querySelector(".px-tabs").addEventListener("keydown", (event) => {
      const at = tabButtons.indexOf(document.activeElement);
      if (at < 0) return;
      let to = -1;
      if (event.key === "ArrowRight") to = (at + 1) % tabButtons.length;
      else if (event.key === "ArrowLeft") to = (at + tabButtons.length - 1) % tabButtons.length;
      else if (event.key === "Home") to = 0;
      else if (event.key === "End") to = tabButtons.length - 1;
      if (to < 0) return;
      event.preventDefault();
      openTab(tabButtons[to].dataset.pxTab);
      tabButtons[to].focus();
    });
    section.addEventListener("click", (event) => {             // «6 сайтов по MD5», «манга из папки…» of the plan
      const go = event.target.closest("[data-px-go]");
      if (!go) return;
      event.preventDefault();
      openTab(go.dataset.pxGo);
    });
    window.addEventListener("hashchange", () => showTab(tabOfAddress()));
    showTab(tabOfAddress());

    // ── the journal ──
    function channelLabel(ch) {
      if (ch === "all") return "Всё";
      if (ch === "final") return "Итоги файлов";
      if (ch === "manga") return "Манга";          // (v5.44.125) «МАНГА:», «НЕ МАНГА:», «ЖДЁТ:», «СБОРКА:»
      if (ch === "reverse") return "Обратный поиск";
      if (ch === "main") return "Общее";
      return ch.startsWith("site:") ? ch.slice(5) : ch;
    }

    function renderTabs() {
      const wanted = ["all", "final", "manga", ...[...channels].filter((c) => c.startsWith("site:")).sort(), "reverse"]
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

    function logShown() {                    // no line yet: the note instead of an empty box
      const any = lines.length > 0;
      pre.hidden = tabs.hidden = !any;
      noLog.hidden = any;
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
      logShown();
      if (stick) pre.scrollTop = pre.scrollHeight;
      renderTabs();
    }

    // ── what the sites have said of a file: [[a site or a service, its last word]] → the chips of its card ──
    // the words of the run (core/parser/engine.py, jobs.py): «MD5» and «поиск» — being asked, «найдено N», «нет»,
    // «ошибка», «отложено» (a service that could not answer), «очередь» — not asked yet
    const CHIP_ORDER = { found: 0, busy: 1, error: 2, wait: 3, miss: 4 };
    function chipsOf(sites) {
      const out = [];
      const list = Array.isArray(sites) ? sites : [];
      const misses = list.filter(([label, word]) => word === "нет" && !services.has(label));
      const fold = misses.length >= 3;                   // «5 сайтов нет» instead of five chips
      list.forEach(([label, said]) => {
        const word = String(said || "");
        if (word === "нет") {
          if (!fold || services.has(label)) out.push({ name: label, state: "miss", said: "нет" });
        } else if (word.startsWith("найдено")) {
          const n = parseInt(word.slice(7), 10);
          out.push({ name: label, state: "found", said: n > 0 ? tagsWord(n) : "нашёл" });
        } else if (word.startsWith("ошибка")) {
          out.push({ name: label, state: "error", said: "ошибка" });
        } else if (word === "отложено") {
          out.push({ name: label, state: "miss", said: "не ответил" });
        } else if (word === "очередь") {
          out.push({ name: label, state: "wait", said: "в очереди" });
        } else {
          out.push({ name: label, state: "busy", said: "спрашиваю" });
        }
      });
      out.sort((a, b) => CHIP_ORDER[a.state] - CHIP_ORDER[b.state]);
      if (fold) out.push({ name: sitesWord(misses.length), state: "miss", said: "нет" });
      return out;
    }

    function chipNode(chip) {
      const li = document.createElement("li");
      li.className = "lane lane-" + chip.state;
      li.append(chip.name + " ");
      const small = document.createElement("small");
      small.textContent = chip.said;
      li.appendChild(small);
      return li;
    }

    // a file in work in a line: where it is (jobs.py: stage)
    function saidOf(item) {
      const sites = Array.isArray(item.sites) ? item.sites : [];
      const found = sites.filter(([, word]) => String(word).startsWith("найдено")).length;
      const waits = sites.filter(([, word]) => word === "очередь" || word === "MD5").length;
      if (item.stage === "sites") {
        if (found) return `найден на ${found} ${found === 1 ? "сайте" : "сайтах"}, ждёт ещё ${waits}`;
        const answered = sites.length - waits;
        return answered ? `сайты спрашиваются: ответили ${answered} из ${sites.length}` : "сайты спрашиваются";
      }
      if (item.stage === "write") return "найден — записываю в библиотеку";
      if (item.stage === "after") return "по MD5 нигде нет — проверяю имя файла и галерею";
      if (item.stage === "reverse") return "по MD5 нигде нет — ищу похожую картинку";
      return "читается: MD5 и что о нём знает библиотека";
    }

    function kindBadge(item) {
      if (item.kind !== "video" && item.kind !== "gif") return null;
      const b = document.createElement("span");
      b.className = "kind-badge";
      b.textContent = item.kind === "video" ? "видео" : "gif";
      return b;
    }

    // ── «В работе»: a card per file, kept while the file is in work (its picture loads once) ──
    function renderFlight(more) {
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
          const text = document.createElement("div");
          text.className = "flight-text";
          const name = document.createElement("p");
          name.className = "flight-name";
          name.textContent = item.name;
          name.title = item.name;
          const said = document.createElement("p");
          said.className = "flight-said";
          const chips = document.createElement("ul");
          chips.className = "lane-chips";
          text.append(name, said, chips);
          card.append(pic, text);
        }
        if (cardsBox.children[index] !== card) cardsBox.insertBefore(card, cardsBox.children[index] || null);
        const list = chipsOf(item.sites);
        const key = item.stage + "|" + list.map((c) => c.name + ":" + c.state + ":" + c.said).join("|");
        if (card.dataset.said !== key) {              // the same: not drawn again
          card.dataset.said = key;
          card.dataset.stage = item.stage || "read";
          card.querySelector(".flight-said").textContent = saidOf(item);
          const chips = card.querySelector(".lane-chips");
          chips.textContent = "";
          list.forEach((chip) => chips.appendChild(chipNode(chip)));
        }
      });
      const live = running(lastStatus);
      const all = flight.length + more;
      section.querySelector("[data-px-flight-n]").textContent = !flight.length ? ""
        : more > 0 ? `показаны ${flight.length} из ${num(all)} — нажмите на картинку, чтобы открыть крупно`
          : `${filesWord(flight.length)} — нажмите на картинку, чтобы открыть крупно`;
      flightEmpty.hidden = flight.length > 0 || !live;
      flightMore.hidden = more <= 0;
      flightMore.textContent = more > 0 ? `…и ещё ${num(more)} — ждут ответа сайтов: их очереди — на вкладке «Сайты»` : "";
    }

    // ── «Только что»: the last answers, each with what it came to ──
    function renderRecent(live) {
      const shown = recent.slice(0, RECENT_SHOWN);
      recentPart.hidden = shown.length === 0;
      recentPart.querySelector("h2").firstChild.textContent = live ? "Только что " : "Последние файлы запуска ";
      section.querySelector("[data-px-recent-n]").textContent = shown.length ? "последние " + shown.length : "";
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
          b.append(img, mark);
          li.appendChild(b);
        }
        const word = OUTCOME[item.status] || item.status;
        const caption = item.status === "tagged" ? (item.tags ? tagsWord(item.tags) : "найден") : word;
        li.className = "recent-item st-" + item.status;
        li.querySelector(".st-badge").textContent = caption;
        const what = word + (item.status === "tagged" && item.tags ? `, тегов: ${item.tags}` : "");
        li.querySelector("button").title = item.name + " — " + what;
        li.querySelector("button").setAttribute("aria-label", item.name + " — " + what + ". Открыть крупно");
        if (recentBox.children[index] !== li) recentBox.insertBefore(li, recentBox.children[index] || null);
      });
    }

    // ── a site switched off for the run: why, and «Проверить снова» once the person has fixed it ──
    const alertBox = section.querySelector(".run-alert");
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

    // ── «Сайты»: one table of the queues — who, how it is asked, how many wait, its pace, how long, how much done ──
    const STAGE_NAMES = { read: "Чтение", writes: "Запись в библиотеку", after: "Проверки после сайтов" };
    const stageOf = (q) => (q.stage === "writes" || q.stage === "after" ? q.stage : "read");

    function perTask(seconds) {
      return (seconds < 0.1 ? Math.round(seconds * 1000) + " мс" : seconds.toFixed(1).replace(".", ",") + " с") + " на файл";
    }

    function queueState(q) {
      if (q.how === "выключен") return "off";
      if (q.how === "пауза") return "paused";
      if (q.kind === "files") return (q.stage === "read" ? q.reading : q.waiting) > 0 ? "busy" : "idle";
      return q.waiting > 0 || q.file ? "busy" : "idle";
    }

    function renderQueues(state, live) {
      const all = state.queues || [];
      queuesPart.hidden = all.length === 0;
      willPart.hidden = all.length > 0;
      const groups = {
        files: all.filter((q) => q.kind === "files"),         // the files' own stages: read, written, checked after
        site: all.filter((q) => q.kind !== "reverse" && q.kind !== "files"),
        reverse: all.filter((q) => q.kind === "reverse"),
      };
      queueBoxes.files.hidden = groups.files.length === 0;
      queueBoxes.reverse.hidden = groups.reverse.length === 0;
      const slowest = live && state.eta != null && state.eta > 0 ? state.eta_by || "" : "";
      Object.keys(groups).forEach((kind) => fillQueues(queueBoxes[kind], groups[kind], slowest, live));
      if (all.length) {
        const counter = section.querySelector('[data-px-n="sites"]');
        if (counter) counter.textContent = String(groups.site.length + groups.reverse.length);
      }
    }

    function cell(tag, cls) {
      const el = document.createElement(tag);
      if (cls) el.className = cls;
      return el;
    }

    function fillQueues(box, list, slowest, live) {
      const names = new Set(list.map((q) => q.site));
      box.querySelectorAll("tr.site-queue").forEach((tr) => { if (!names.has(tr.dataset.site)) tr.remove(); });
      list.forEach((q, index) => {
        let tr = box.querySelector(`tr.site-queue[data-site="${CSS.escape(q.site)}"]`);
        if (!tr) {
          tr = document.createElement("tr");
          tr.dataset.site = q.site;
          const who = cell("th");
          who.scope = "row";
          who.append(cell("span", "q-site"), cell("span", "px-tag"));
          const how = cell("td", "q-wide");
          how.append(cell("span", "q-how"), cell("span", "q-now"));
          const done = cell("td", "q-done q-rest");
          const meter = cell("span", "px-meter");
          meter.appendChild(document.createElement("i"));
          done.append(meter, cell("small", "q-count"));
          tr.append(who, how, cell("td", "n q-wait"), cell("td", "n q-rate q-rest"), cell("td", "n q-left"), done);
        }
        const state = queueState(q);
        const slow = !!slowest && (q.name || q.site) === slowest;
        tr.className = "site-queue q-" + state + (slow ? " slow" : "");
        tr.querySelector(".q-site").textContent = q.kind === "files" ? STAGE_NAMES[stageOf(q)] : q.site;
        const tag = tr.querySelector(".px-tag");
        const keeps = live && q.kind !== "files" && state !== "off" && state !== "paused" && q.waiting === 0 && q.answered > 0;
        tag.hidden = !(slow || keeps);
        tag.className = "px-tag" + (slow ? "" : " calm");
        tag.textContent = slow ? "дольше всех" : "успевает";
        // how it is asked; the files' own stages — with what they are doing and how long a file takes
        let how = q.how || "";
        if (q.kind === "files" && q.stage === "read" && q.reading > 0) how += ", сейчас читаются " + q.reading;
        if (q.kind === "files" && q.stage !== "read" && q.average > 0) how += ", " + perTask(q.average);
        tr.querySelector(".q-how").textContent = how;
        const now = tr.querySelector(".q-now");
        if (q.kind === "files") now.textContent = "";
        else if (state === "off") now.textContent = q.reason || "";
        else now.textContent = q.file ? (q.kind === "reverse" ? "ищет: " : "спрашивает: ") + q.file
          : (state === "paused" ? "ждёт конца паузы" : "");
        now.title = now.textContent;
        tr.querySelector(".q-wait").textContent = num(q.waiting);
        const pace = Number(q.per_minute) || 0;
        tr.querySelector(".q-rate").textContent = pace <= 0 ? "—"
          : pace < 10 ? pace.toFixed(1).replace(".", ",") : num(pace);
        // how long its work still takes, with the files not read yet (jobs.py: left)
        const left = tr.querySelector(".q-left");
        left.title = "";
        if (!live || state === "off") left.textContent = "—";
        else if (q.left == null) {
          left.textContent = "считаю…";
          left.title = "Станет ясно, когда очередь разгонится";
        } else if (q.left >= 60) left.textContent = "≈ " + minutes(q.left);
        else if (q.left > 0 || q.waiting > 0) left.textContent = "меньше минуты";
        else left.textContent = q.kind === "files" && q.stage === "read" ? "всё прочитано" : "успевает";
        const share = q.answered + q.waiting > 0 ? q.answered / (q.answered + q.waiting) : 0;
        tr.querySelector(".px-meter i").style.width = Math.round(100 * share) + "%";
        tr.querySelector(".q-count").textContent = num(q.answered);
        const at = index + 1;                             // the first row of the group is its title
        if (box.children[at] !== tr) box.insertBefore(tr, box.children[at] || null);
      });
    }

    // ── on top: the state, how much is done and left, who holds the run ──
    const stateBox = section.querySelector(".px-state");
    const stateWord = section.querySelector("[data-px-state]");
    const timeLine = section.querySelector("[data-px-time]");
    const etaLine = section.querySelector("[data-px-eta]");
    const hint = section.querySelector("[data-px-hint]");

    function renderBar(state) {
      const bar = section.querySelector(".run-bar");
      const total = Math.max(1, state.total || 0);
      const counts = state.counts || {};
      ["tagged", "nomatch", "deferred", "skip", "error"].forEach((key) => {
        const seg = bar.querySelector(".seg-" + key);
        seg.style.width = Math.min(100, (100 * (counts[key] || 0)) / total) + "%";
        const shown = section.querySelector(`[data-count="${key}"]`);
        if (shown) shown.textContent = num(counts[key] || 0);
      });
      const done = state.done || 0;
      const percent = state.total ? Math.floor((100 * done) / state.total) : 0;
      bar.querySelector(".run-percent").textContent = state.total ? percent + " %" : "";
      bar.setAttribute("aria-valuemax", String(total));
      bar.setAttribute("aria-valuenow", String(done));
      bar.setAttribute("aria-valuetext", state.total ? `${done} из ${state.total}` : "файлы ещё считаются");
    }

    function timeWords(state, status, live) {
      if (status === "busy") return "запущен из другого места" + (state.owner ? " (" + state.owner + ")" : "");
      if (live) return minutes(state.elapsed || 0) + (state.folder ? ` · папка «${state.folder}»` : "");
      const run = state.id ? state : state.last;               // the run in memory, else the note of the last one
      if (!run || !run.finished) return "";
      const lasted = run.started && run.finished >= run.started ? ", шёл " + minutes(run.finished - run.started) : "";
      return (state.id ? "" : "прошлый запуск — ") + ago(run.finished) + lasted;
    }

    function renderEta(state, status) {
      etaLine.textContent = "";
      if (status === "starting") etaLine.textContent = "файлы считаются…";
      else if (status === "stopping") etaLine.textContent = "доделываю начатые файлы, новые не беру";
      else if (state.eta != null) {
        const b = document.createElement("b");
        b.textContent = minutes(state.eta);
        etaLine.append("осталось ≈ ", b);
      } else if ((state.total || 0) > (state.done || 0)) {
        // not known yet: the stage that has work and no pace is named
        etaLine.textContent = "сколько осталось — станет ясно, когда " + (state.eta_by ? "разгонится: " + state.eta_by
          : "очереди разгонятся");
      }
    }

    // who holds the run: the slowest queue by name, how it is asked and its pace
    function renderHint(state, live) {
      const slow = live && state.eta != null && state.eta > 0 && state.eta_by
        ? (state.queues || []).find((q) => (q.name || q.site) === state.eta_by) : null;
      hint.hidden = !slow;
      if (!slow) return;
      const who = slow.kind === "files" ? STAGE_NAMES[stageOf(slow)].toLowerCase() : slow.site;
      const pace = Number(slow.per_minute) || 0;
      const paceWords = pace > 0 ? `, ${pace < 10 ? pace.toFixed(1).replace(".", ",") : num(pace)} в минуту` : "";
      const b = document.createElement("b");
      b.textContent = "Дольше всех — " + who + ":";
      hint.textContent = "";
      hint.append(b, " " + (slow.how || "") + paceWords + ". " + (slow.kind === "files"
        ? "Сайты успевают за файлами."
        : "Остальные его не ждут — найденное на них уже в галерее."));
    }

    // «Итог»: what the run came to — the run in memory, else the note of the last one (a program started anew)
    function renderLast(state, live) {
      const run = state.id ? state : state.last;
      const any = !live && !!run && !!run.finished;
      lastPart.hidden = !any;
      idle.hidden = live || any;
      if (!any) return;
      lastPart.querySelector("h2").firstChild.textContent = state.id ? "Этот запуск " : "Прошлый запуск ";
      section.querySelector("[data-px-last-when]").textContent = run.started ? span(run.started, run.finished) : ago(run.finished);
      const counts = run.counts || {};
      Object.keys(OUTCOME).forEach((key) => {
        const shown = lastPart.querySelector(`[data-last="${key}"]`);
        if (shown) shown.textContent = num(counts[key] || 0);
      });
      const stopped = state.id ? !!(state.summary && state.summary.stopped) : !!run.stopped;
      const done = `обработано ${num(run.done || 0)} из ${num(run.total || 0)} файлов`;
      const said = [];
      if (run.status === "failed") said.push(`Запуск остановлен ошибкой: ${done}` + (state.id ? " — причина в журнале." : "."));
      else if (stopped) said.push(`Остановлен вручную: ${done} — остальные возьмёт следующий запуск.`);
      if (counts.deferred) said.push(`Отложенные ${num(counts.deferred)} — сайт или сервис не смог ответить (лимит, пауза): следующий запуск спросит их снова.`);
      if (counts.error && state.id) said.push(`Ошибки ${num(counts.error)} — что с каждым файлом, написано в журнале.`);
      section.querySelector("[data-px-last-said]").textContent = said.join(" ");
    }

    function render(state) {
      const status = state.status || "idle";
      const live = running(status);
      if (state.id && state.id !== shownJob) {            // another run: its journal and pictures afresh
        shownJob = state.id;
        lines.length = 0;
        channels.clear();
        pre.textContent = "";
        tabs.textContent = "";
        cardsBox.textContent = "";
        recentBox.textContent = "";
        Object.values(queueBoxes).forEach((box) => box.querySelectorAll("tr.site-queue").forEach((tr) => tr.remove()));
      }
      lastStatus = status;
      section.dataset.status = status;
      section.dataset.ready = "1";                        // the first answer of the program is drawn
      stateBox.className = "px-state" + (status === "paused" ? " paused" : status === "failed" ? " failed" : live ? "" : " idle");
      stateWord.textContent = STATUS[status] || status;
      timeLine.textContent = timeWords(state, status, live);
      const start = button("parser-start"), pause = button("parser-pause"), stop = button("parser-stop");
      start.hidden = live;
      start.disabled = live || status === "busy";
      pause.hidden = stop.hidden = !live;
      pause.disabled = stop.disabled = !(status === "running" || status === "paused");
      pause.textContent = status === "paused" ? "Продолжить" : "Пауза";
      pause.dataset.paused = status === "paused" ? "1" : "";
      section.querySelectorAll("[data-px-when]").forEach((el) => { el.hidden = (el.dataset.pxWhen === "run") !== live; });
      section.querySelector("#px-tab-now [data-px-tab-words]").textContent = live ? "Сейчас" : "Итог";
      services = new Set((state.queues || []).filter((q) => q.kind === "reverse").map((q) => q.site));
      if (live) {
        section.querySelector("[data-px-done]").textContent = num(state.done || 0);
        section.querySelector("[data-px-total]").textContent = state.total ? "из " + num(state.total) + " "
          + plural(state.total, "файла", "файлов", "файлов") : "";
        renderBar(state);
        renderEta(state, status);
      }
      renderHint(state, live);
      renderLast(state, live);
      flight = state.id ? state.files || [] : [];
      recent = state.id ? state.recent || [] : [];
      flightPart.hidden = !live;
      renderFlight(live ? Number(state.files_more || 0) : 0);
      renderRecent(live);
      renderQueues(state, live);
      renderAlert(state.queues || [], status);
      if (viewer.open && pv.list === "flight") viewerLanes();
      // only lines not shown yet: a poll from a button and a timed one can carry the same lines
      const fresh = (state.log || []).filter((l) => l.seq > after);
      if (fresh.length) appendLog(fresh);
      else logShown();
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
        const word = OUTCOME[item.status] || item.status;
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

    // what the sites have said of the picture shown, as the last answer says (kept fresh while the viewer is open)
    function viewerLanes() {
      const item = viewerItem();
      if (!item) return;
      const chips = viewer.querySelector(".pv-chips");
      const now = flight.find((f) => String(f.id) === String(item.id));
      const what = viewer.querySelector(".pv-what");
      chips.textContent = "";
      if (!now) {
        what.textContent = "Готово — ответ в «Только что».";
        return;
      }
      const said = saidOf(now);
      what.textContent = said[0].toUpperCase() + said.slice(1) + ".";
      chipsOf(now.sites).forEach((chip) => chips.appendChild(chipNode(chip)));
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
          openTab("now");                    // the files in work: what the run is doing
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
    logShown();
    poll();
  }
})();
