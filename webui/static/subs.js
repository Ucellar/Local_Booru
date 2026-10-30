// Local Booru page «Подписки»: the subscriptions, the check in progress (polled from the program),
// «Скачать тег» and the blocklist.
(() => {
  "use strict";

  const LB = window.LocalBooru;
  const section = document.getElementById("subs");
  if (!LB || !section) return;
  const { actions, postJson, say } = LB;
  const confirmed = LB.confirmed || (() => true);
  const data = JSON.parse((document.getElementById("subs-data") || {}).textContent || "{}");
  const job = section.querySelector("[data-job]");
  const pre = job.querySelector(".job-log");
  const planBox = job.querySelector(".plan-box");
  const dialog = section.querySelector(".sub-dialog");
  const form = dialog.querySelector('form[data-form="sub"]');
  const tagForm = section.querySelector('form[data-form="tag"]');
  const field = (name) => form.elements.namedItem(name);
  const button = (name) => section.querySelector(`[data-action="${name}"]`);
  const STATUS = {
    starting: "запускается", running: "идёт", waiting: "ждёт ответа", stopping: "останавливается",
    done: "закончена", failed: "остановлена ошибкой", idle: "", busy: "занято",
  };
  const running = (status) => ["starting", "running", "waiting", "stopping"].includes(status);
  const LOG_KEEP = 3000;
  const lines = [];
  let after = 0;
  let shownJob = 0;
  let timer = 0;
  let lastStatus = "";

  function minutes(sec) {
    if (sec == null) return "";
    if (sec < 60) return sec + " с";
    const m = Math.round(sec / 60);
    return m < 60 ? m + " мин" : Math.floor(m / 60) + " ч " + (m % 60) + " мин";
  }

  function bytes(n) {
    if (!n) return "0 Б";
    const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return (v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)) + " " + units[i];
  }

  // ── the dialog of a subscription ───────────────────────────────────────
  function siteRows(chosen) {
    const body = dialog.querySelector(".pick-table tbody");
    const picked = new Map(chosen.map((s) => [s.site, s]));
    const names = [...new Set([...(data.sites || []), ...picked.keys()])];
    body.textContent = "";
    names.forEach((site) => {
      const pick = picked.get(site);
      const row = document.createElement("tr");
      row.dataset.site = site;
      row.classList.toggle("off", !pick);
      const use = document.createElement("input");
      use.type = "checkbox";
      use.checked = !!pick;
      use.setAttribute("aria-label", "Искать на " + site);
      use.addEventListener("change", () => row.classList.toggle("off", !use.checked));
      const priority = document.createElement("select");
      priority.setAttribute("aria-label", "Приоритет " + site);
      for (let p = 5; p >= 1; p -= 1) {
        const option = document.createElement("option");
        option.value = String(p);
        option.textContent = String(p);
        priority.appendChild(option);
      }
      priority.value = String((pick && pick.priority) || (data.priority || {})[site] || 3);
      const override = document.createElement("input");
      override.type = "text";
      override.value = (pick && pick.query_override) || "";
      override.placeholder = "как общий";
      override.spellcheck = false;
      override.setAttribute("aria-label", "Свой запрос для " + site);
      [use, site, priority, override].forEach((item) => {
        const cell = document.createElement("td");
        if (typeof item === "string") cell.textContent = item; else cell.appendChild(item);
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
  }

  function fill(sub) {
    field("id").value = sub.id || "";
    field("query").value = sub.query || "";
    field("check_interval_hours").value = sub.check_interval_hours || 24;
    field("max_pages").value = sub.max_pages || 3;
    field("run_mode").value = sub.run_mode || "all";
    field("run_direction").value = sub.run_direction || "newest_to_oldest";
    field("enabled").checked = sub.enabled !== false;
    siteRows(sub.sites || []);
    dialog.querySelector("#sub-dialog-title").textContent = sub.id ? "Подписка: " + sub.query : "Новая подписка";
    button("sub-reset").hidden = !sub.id;
    button("sub-clear-open").hidden = !sub.id;
    button("sub-delete").hidden = !sub.id;
    const status = dialog.querySelector(".action-status");
    if (status) status.textContent = "";
  }

  function defaults(query, sites) {
    const chosen = (sites || data.parser_on || []).map((site) => ({ site, priority: (data.priority || {})[site] || 3 }));
    return { query: query || "", sites: chosen, check_interval_hours: 24, max_pages: 3, run_mode: "all",
      run_direction: "newest_to_oldest", enabled: true };
  }

  function formData() {
    const sites = [];
    dialog.querySelectorAll(".pick-table tbody tr").forEach((row) => {
      const [use, priority, override] = row.querySelectorAll("input, select");
      if (use.checked) sites.push({ site: row.dataset.site, priority: Number(priority.value), query_override: override.value.trim() });
    });
    return {
      query: field("query").value.trim(), sites,
      check_interval_hours: Number(field("check_interval_hours").value), max_pages: Number(field("max_pages").value),
      run_mode: field("run_mode").value, run_direction: field("run_direction").value, enabled: field("enabled").checked,
    };
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await postJson("/api/subs/save", { id: field("id").value, form: formData() });
      location.reload();
    } catch (err) {
      say(form.querySelector('button[type="submit"]'), err.message, true);
    }
  });

  // ── the table ──────────────────────────────────────────────────────────
  section.addEventListener("change", async (event) => {
    const box = event.target.closest("[data-sub-toggle]");
    if (!box) return;
    const row = box.closest("tr");
    try {
      await postJson("/api/subs/toggle", { id: box.dataset.id, enabled: box.checked });
      row.classList.toggle("off", !box.checked);
      if ((data.subs || {})[box.dataset.id]) data.subs[box.dataset.id].enabled = box.checked;
      poll();
    } catch (err) {
      box.checked = !box.checked;
      say(section.querySelector(".tool-actions"), err.message, true);
    }
  });

  function updateRows(rows) {
    Object.entries(rows || {}).forEach(([id, state]) => {
      const row = section.querySelector(`.subs-table tr[data-id="${CSS.escape(id)}"]`);
      if (!row) return;
      row.classList.toggle("running", !!state.running);
      row.classList.toggle("off", !state.enabled);
      ["last", "next", "downloaded"].forEach((k) => {
        const cell = row.querySelector(`[data-cell="${k}"]`);
        if (cell && cell.textContent !== String(state[k])) cell.textContent = String(state[k]);
      });
      const error = row.querySelector('[data-cell="error"]');
      if (error) {
        error.textContent = state.error || "";
        error.hidden = !state.error;
      }
    });
  }

  // ── the check in progress ──────────────────────────────────────────────
  function appendLog(fresh) {
    fresh = (fresh || []).filter((l) => l.seq > after);   // two answers at once carry the same lines
    if (!fresh.length) return;
    const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    fresh.forEach((l) => lines.push(l.text));
    if (lines.length > LOG_KEEP) {
      lines.splice(0, lines.length - LOG_KEEP);
      pre.textContent = lines.join("\n");
    } else {
      const text = fresh.map((l) => l.text).join("\n");
      pre.textContent += (pre.textContent ? "\n" : "") + text;
    }
    if (stick) pre.scrollTop = pre.scrollHeight;
  }

  let shownPlan = null;                       // the question on the page: {job, seq} — the answer names it

  function renderPlan(state) {
    const plan = state.plan || {};
    const asking = state.status === "waiting" && plan.files;
    planBox.hidden = !asking;
    shownPlan = asking ? { job: state.id, seq: plan.seq } : null;
    if (!asking) return;
    planBox.querySelector(".plan-name").textContent = plan.name ? "— " + plan.name : "";
    let text = `Найдено файлов для загрузки: ${plan.files}. Известный размер: ${bytes(plan.known_bytes)}` +
      ` (для ${plan.known_files} файлов). Свободно на диске: ${bytes(plan.disk_free)}.` +
      " Сайты отдают файлы небыстро — загрузка может занять много времени.";
    if (plan.not_enough_space) text += " Внимание: известный размер уже больше свободного места с запасом.";
    planBox.querySelector(".plan-text").textContent = text;
  }

  function render(state) {
    const status = state.status || "idle";
    const busy = running(status) || status === "busy";
    const runAll = button("subs-run-all");
    runAll.disabled = busy || !Object.keys(data.subs || {}).length;
    section.querySelectorAll('[data-action="sub-run"]').forEach((b) => { b.disabled = busy; });
    tagForm.querySelector('button[type="submit"]').disabled = busy;
    button("subs-stop").disabled = !["starting", "running", "waiting"].includes(status);
    updateRows(state.subs);
    const note = section.querySelector(".auto-note");
    if (note && state.auto_note && note.textContent !== state.auto_note) note.textContent = state.auto_note;
    if (status === "busy") say(runAll, "Подписки проверяются из другого места (" + (state.owner || "?") + ")");
    if (!state.id) { job.hidden = true; return; }
    job.hidden = false;
    if (state.id !== shownJob) {             // another check: its journal from the start
      shownJob = state.id;
      lines.length = 0;
      pre.textContent = "";
    }
    job.querySelector(".job-title").textContent = state.label || "Подписки";
    job.querySelector(".job-status").textContent = STATUS[status] || status;
    job.dataset.status = status;
    job.querySelector('[data-count="downloaded"]').textContent = String(state.downloaded || 0);
    const many = job.querySelector('[data-show="many"]');
    many.hidden = !(state.count > 1);
    job.querySelector('[data-count="index"]').textContent = `${state.index || 0} из ${state.count || 0}`;
    job.querySelector("[data-elapsed-label]").textContent = running(status) ? "идёт" : "заняла";
    job.querySelector('[data-count="elapsed"]').textContent = minutes(state.elapsed);
    job.querySelector(".job-current").textContent = running(status) && state.current ? "Сейчас: " + state.current : "";
    job.querySelector(".job-links").hidden = running(status) || !state.downloaded;
    renderPlan(state);
    appendLog(state.log);
    after = Math.max(after, state.last_seq || 0);
    if (lastStatus && running(lastStatus) && !running(status)) {
      say(button("subs-stop"), status === "failed" ? "Проверка остановлена ошибкой — см. журнал" : "Проверка закончена");
    }
    lastStatus = status;
  }

  let polling = false;
  let pollAgain = false;

  async function poll() {
    clearTimeout(timer);
    if (polling) { pollAgain = true; return; }    // one request at a time; the asked one follows it
    polling = true;
    let delay = 4000;
    const asked = after;
    try {
      const response = await fetch("/api/subs/state?after=" + asked, { credentials: "same-origin" });
      const state = await response.json();
      if (state.id && state.id !== shownJob && asked !== 0) {
        after = 0;                          // a new check since the last look: its journal from the start
        delay = 0;
      } else {
        render(state);
        delay = running(state.status) ? 700 : 4000;
      }
    } catch (err) {
      delay = 4000;
    } finally {
      polling = false;
      if (pollAgain) { pollAgain = false; delay = 0; }
      timer = setTimeout(poll, delay);
    }
  }

  async function start(el, url, body) {
    el.disabled = true;
    try {
      await postJson(url, body);
      say(el, "Запущено");
      after = 0;
      poll();
    } catch (err) {
      say(el, err.message, true);
      el.disabled = false;
    }
  }

  async function answerPlan(el, allow) {
    if (!shownPlan) return;
    try {
      await postJson("/api/subs/decide", { allow, job: shownPlan.job, seq: shownPlan.seq });
    } catch (err) {
      say(el, err.message, true);
    }
    poll();
  }

  // ── «Скачать тег» and the blocklist ────────────────────────────────────
  function tagSites() {
    return Array.from(tagForm.querySelectorAll('input[name="site"]:checked')).map((box) => box.value);
  }

  tagForm.addEventListener("submit", (event) => {
    event.preventDefault();
    start(tagForm.querySelector('button[type="submit"]'), "/api/subs/tag", {
      query: tagForm.elements.namedItem("query").value, sites: tagSites(),
      limit: Number(tagForm.elements.namedItem("limit").value),
    });
  });

  const blockForm = section.querySelector('form[data-form="blocklist"]');
  blockForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = blockForm.querySelector('button[type="submit"]');
    try {
      const r = await postJson("/api/subs/blocklist", { text: blockForm.elements.namedItem("blocklist").value });
      say(submit, r.tags ? `Сохранено: тегов — ${r.tags}` : "Сохранено: блоклист пуст");
    } catch (err) {
      say(submit, err.message, true);
    }
  });

  // ── «Удалить скачанные»: the sessions of a subscription to the trash (a task of the page, its journal) ──
  const clearDialog = section.querySelector(".clear-dialog");
  const clearForm = clearDialog.querySelector('form[data-form="clear"]');
  const clearList = clearDialog.querySelector(".clear-sessions");
  const clearJob = clearDialog.querySelector("[data-clear-job]");
  let clearWatcher = null;
  const clearMode = () => (clearForm.querySelector('input[name="mode"]:checked') || {}).value || "all";

  function showPicks() {
    clearList.querySelectorAll("input").forEach((box) => { box.disabled = clearMode() !== "pick"; });
  }
  clearForm.addEventListener("change", (event) => { if (event.target.name === "mode") showPicks(); });

  async function openClear(id) {
    clearForm.elements.namedItem("id").value = id;
    clearList.textContent = "Смотрю папки…";
    if (!clearDialog.open) clearDialog.showModal();
    try {
      const r = await fetch("/api/subs/sessions?id=" + encodeURIComponent(id), { credentials: "same-origin" });
      const answer = await r.json();
      if (!answer.ok) throw new Error(answer.error || "Программа не ответила");
      clearDialog.querySelector("#clear-title").textContent = "Удалить скачанные: " + answer.query;
      const shared = clearDialog.querySelector(".clear-shared");
      const others = answer.shared_with || [];
      shared.hidden = !others.length;
      shared.textContent = others.length ? "Эти папки общие с подписками: " + others.join(", ")
        + " — их файлы в выбранных сессиях тоже уйдут в корзину." : "";
      clearList.textContent = "";
      if (!answer.sessions.length) { clearList.textContent = "В папках этой подписки скачанных файлов нет."; return; }
      answer.sessions.forEach((item) => {
        const li = document.createElement("li");
        const label = document.createElement("label");
        label.className = "check";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.value = item.key;
        label.append(box, ` ${item.name} — ${item.site} — ${item.files} файл., ${bytes(item.bytes)}`);
        li.appendChild(label);
        clearList.appendChild(li);
      });
      showPicks();
    } catch (err) {
      clearList.textContent = err.message;
    }
  }

  function watchClear() {
    clearWatcher = clearWatcher || LB.watchTask(clearJob, "/api/subs/clear/state", {
      onState(st, busy) { clearDialog.querySelector('[data-action="sub-clear-stop"]').hidden = !busy; },
      onEnd(st) {
        const where = button("sub-clear");
        if (st.status === "failed") say(where, st.error || "Не получилось — см. журнал", true);
        else say(where, st.status === "stopped" ? "Остановлено — остальные файлы на месте" : "Готово — файлы в корзине");
        openClear(clearForm.elements.namedItem("id").value);
        poll();
      },
    });
    return clearWatcher;
  }

  Object.assign(actions, {
    "sub-clear-open"() {
      const id = field("id").value;
      dialog.close();
      openClear(id);
    },
    "sub-clear-close": () => clearDialog.close(),
    async "sub-clear-stop"(el) {
      try {
        await postJson("/api/subs/clear/stop", {});
      } catch (err) {
        say(el, err.message, true);
      }
    },
    async "sub-clear"(el) {
      const keys = Array.from(clearList.querySelectorAll("input:checked")).map((box) => box.value);
      if (clearMode() === "pick" && !keys.length) { say(el, "Отметьте сессии", true); return; }
      if (!confirmed(el, "В корзину? Нажмите ещё раз")) return;
      el.disabled = true;
      try {
        await postJson("/api/subs/clear", { id: clearForm.elements.namedItem("id").value, mode: clearMode(), keys });
        say(el, "Переношу…");
        watchClear().started();
      } catch (err) {
        say(el, err.message, true);
      } finally {
        el.disabled = false;
      }
    },
  });

  Object.assign(actions, {
    "sub-add"() {
      fill(defaults());
      dialog.showModal();
      field("query").focus();
    },
    async "sub-edit"(el) {
      let sub = (data.subs || {})[el.dataset.id];
      try {                                 // as it is now: «Авто» or the old page may have changed it
        const r = await fetch("/api/subs/sub?id=" + encodeURIComponent(el.dataset.id), { credentials: "same-origin" });
        const answer = await r.json();
        if (answer.ok && answer.sub) sub = data.subs[el.dataset.id] = answer.sub;
        else if (r.status === 404) { say(el, "Этой подписки уже нет — обновите страницу", true); return; }
      } catch (err) { /* the page's copy will do */ }
      fill(sub || defaults());
      dialog.showModal();
    },
    "sub-dialog-close": () => dialog.close(),
    "tag-to-sub"() {
      fill(defaults(tagForm.elements.namedItem("query").value.trim(), tagSites()));
      dialog.showModal();
      field("query").focus();
    },
    async "sub-delete"(el) {
      if (!confirmed(el, "Удалить подписку? Нажмите ещё раз")) return;
      try {
        await postJson("/api/subs/delete", { id: field("id").value });
        location.reload();
      } catch (err) { say(el, err.message, true); }
    },
    async "sub-reset"(el) {
      if (!confirmed(el, "Сбросить? Нажмите ещё раз")) return;
      try {
        await postJson("/api/subs/reset", { id: field("id").value });
        say(el, "Прогресс сброшен: следующая проверка просмотрит посты заново");
        poll();
      } catch (err) { say(el, err.message, true); }
    },
    "sub-run": (el) => start(el, "/api/subs/run", { id: el.dataset.id }),
    "subs-run-all": (el) => start(el, "/api/subs/run", { all: true }),
    async "subs-stop"(el) {
      if (!confirmed(el, "Остановить? Нажмите ещё раз")) return;
      try {
        await postJson("/api/subs/stop");
        poll();
      } catch (err) { say(el, err.message, true); }
    },
    "subs-plan-yes": (el) => answerPlan(el, true),
    "subs-plan-no": (el) => answerPlan(el, false),
  });

  poll();
})();
