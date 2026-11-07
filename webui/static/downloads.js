// Local Booru «Скачать → Загрузки» (v5.44.120, webui/pages/downloads.py): everything that downloads in one place —
// the manga and manhwa of the sites, nhentai, the videos, «Галерея (booru)» and the subscriptions; one queue with its
// places and ↑↓; «Готово» for 7 days with «Повторить не скачавшееся»; «Сама программа» with its switches.  The
// computer (/get/downloads) and the phone (/m/get/downloads) have the same board, this script and one API:
// /api/downloads/state (asked every 1.5 s while the page is seen, never while it is hidden), /done (when it changed),
// /duties (every 15 s), /log (a journal opened), /act, /all, /retry, /duty, /duty-act.  «SFW» is the server's: a row it
// hides comes as «Скрытая загрузка 18+» with its numbers and buttons only.  Every rule of downloads.css starts with
// .dl (the looks style buttons by their tag).
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const box = document.getElementById("downloads");
  if (!box || !LB.postJson) return;
  const phone = box.dataset.phone === "1";
  const say = (text, bad) => (LB.say ? LB.say(null, text, bad) : null);
  const countLine = box.querySelector(".dl-count");
  const headTools = box.querySelector(".dl-head-tools");
  const pauseAll = headTools.querySelector('[data-all="pause"]');
  const stopAll = headTools.querySelector('[data-all="stop"]');
  const going = box.querySelector(".dl-going");
  const jobsList = going.querySelector(".dl-jobs");
  const goingEmpty = going.querySelector(".dl-empty");
  const queuePanel = box.querySelector(".dl-queue-panel");
  const queueList = queuePanel.querySelector(".dl-queue");
  const donePanel = box.querySelector(".dl-done-panel");
  const doneList = donePanel.querySelector(".dl-done");
  const doneEmpty = donePanel.querySelector(".dl-done-empty");
  const doneAll = donePanel.querySelector("[data-done-all]");
  const dutiesList = box.querySelector(".dl-duties");
  const STATES = { running: "идёт", paused: "пауза", stopping: "останавливается", waiting: "ждёт ответа",
    restoring: "с прошлого запуска" };
  const ICON = {
    pause: ["M8 5v14", "M16 5v14"], play: ["M7 4.5v15l12-7.5z"], skip: ["M5 5v14l10-7z", "M19 5v14"],
    stop: ["M6 6h12v12H6z"], retry: ["M4 12a8 8 0 1 0 2.4-5.7", "M4 4v4.6h4.6"], check: ["M5 12.5l4.5 4.5L19 7.5"],
    warn: ["M12 4l9 16H3z", "M12 10v4.5", "M12 17.5h.01"], up: ["M12 19V5", "M6 11l6-6 6 6"],
    down: ["M12 5v14", "M6 13l6 6 6-6"], list: ["M9 6h11", "M9 12h11", "M9 18h11", "M4.5 6h.01", "M4.5 12h.01",
      "M4.5 18h.01"], close: ["M6 6l12 12", "M18 6L6 18"],
  };

  // ── little things ─────────────────────────────────────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svg(paths, size) {
    const ns = "http://www.w3.org/2000/svg";
    const icon = document.createElementNS(ns, "svg");
    const attrs = { class: "dl-svg", viewBox: "0 0 24 24", width: String(size || 15), height: String(size || 15),
      fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
      "aria-hidden": "true", focusable: "false" };
    Object.entries(attrs).forEach(([k, v]) => icon.setAttribute(k, v));
    paths.forEach((d) => {
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      icon.append(path);
    });
    return icon;
  }

  function button(cls, text, icon) {
    const b = el("button", cls);
    b.type = "button";
    if (icon) b.append(svg(ICON[icon], phone ? 16 : 14));
    b.append(el("span", "dl-words", text));
    return b;
  }

  function words(node, text) {
    const span = node.querySelector(".dl-words");
    if (span) span.textContent = text; else node.textContent = text;
  }

  async function getJson(address) {
    let response;
    try {
      response = await fetch(address, { credentials: "same-origin", cache: "no-store" });
    } catch (_) {
      throw new Error(phone ? "Нет связи с компьютером" : "Программа не отвечает");
    }
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) throw new Error(data.error || "Ошибка " + response.status);
    return data;
  }

  async function send(address, body, button_) {
    if (button_) button_.disabled = true;
    try {
      const reply = await LB.postJson(address, body);
      if (reply && reply.message) say(reply.message);
      return reply;
    } catch (err) {
      say(err.message, true);
      return null;
    } finally {
      if (button_) button_.disabled = false;
      poke();
    }
  }

  async function copy(text) {
    if (LB.copyText) return LB.copyText(text);
    try { await navigator.clipboard.writeText(text); return true; } catch (_) { return false; }
  }

  // ── the state: asked while the page is seen ─────────────────────────────────────────────────────────────────────
  let timer = 0;
  let asking = false;
  let doneRev = null;
  let dutiesAt = 0;
  let showAll = false;

  async function tick() {
    clearTimeout(timer);
    if (document.hidden || asking) return;
    asking = true;
    try {
      const st = await getJson("/api/downloads/state");
      paint(st);
      if (st.done_rev !== doneRev) {
        doneRev = st.done_rev;
        loadDone();
      }
      if (Date.now() - dutiesAt > 15000) loadDuties();
    } catch (_) { /* the program busy for a moment: asked again */ }
    asking = false;
    if (!document.hidden) timer = setTimeout(tick, 1500);
  }

  function poke() {
    clearTimeout(timer);
    timer = setTimeout(tick, 120);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimeout(timer); else tick();
  });

  function paint(st) {
    countLine.textContent = st.said || "";
    going.querySelector(".dl-n").textContent = String((st.jobs || []).filter((r) => r.state !== "restoring").length || "");
    const any = (st.jobs || []).some((r) => r.state !== "restoring") || (st.queue || []).length;
    headTools.hidden = !any;
    const pausable = (st.jobs || []).filter((r) => r.pause);
    const resumable = (st.jobs || []).filter((r) => r.resume);
    pauseAll.hidden = !pausable.length && !resumable.length;
    pauseAll.dataset.act = pausable.length ? "pause" : "resume";
    pauseAll.textContent = "";
    pauseAll.append(svg(pausable.length ? ICON.pause : ICON.play, 15),
      el("span", "dl-words", pausable.length ? "Пауза всего" : "Продолжить всё"));
    paintJobs(st.jobs || []);
    paintQueue(st.queue || []);
    document.querySelectorAll("[data-dl-n]").forEach((n) => {     // the badge of the header, the phone's chip
      n.textContent = st.badge ? String(st.badge) : "";
      n.hidden = !st.badge;
    });
  }

  // ── «Идёт сейчас» ───────────────────────────────────────────────────────────────────────────────────────────────
  const rows = {};               // row id -> its parts

  function rowId(r) { return r.key + (r.state === "restoring" ? "-soon" : ""); }

  function jobRow(r) {
    const id = rowId(r);
    if (rows[id]) return rows[id];
    const li = el("li", "dl-job");
    li.dataset.key = r.key;
    const pic = el("span", "dl-pic");
    const letter = el("span", "dl-letter");
    letter.setAttribute("aria-hidden", "true");
    pic.append(letter);
    const body = el("div", "dl-body");
    const top = el("div", "dl-top");
    const kind = el("span", "dl-kind");
    const title = el("b", "dl-title");
    const dest = el("span", "dl-dest");
    top.append(kind, title, dest);
    const text = el("p", "dl-text");
    const bar = el("span", "dl-bar");
    const fill = el("i");
    bar.append(fill);
    const sub = el("p", "dl-sub");
    const logBox = el("div", "dl-log-box");
    logBox.hidden = true;
    const pre = el("pre", "dl-log");
    pre.tabIndex = 0;
    pre.setAttribute("aria-label", "Журнал");
    const logTools = el("div", "dl-log-tools");
    const copyButton = button("dl-link", "Скопировать журнал");
    const copied = el("span", "dl-said");
    copied.setAttribute("role", "status");
    logTools.append(copyButton, copied);
    logBox.append(pre, logTools);
    body.append(top, text, bar, sub, logBox);
    const tools = el("div", "dl-tools");
    const pause = button("dl-btn", "Пауза", "pause");
    const skip = button("dl-btn", "Пропустить", "skip");
    const forget = button("dl-btn", "Не продолжать", "close");
    const more = el("div", "dl-more-tools");
    const details = button("dl-link", "Подробности ▾");
    details.setAttribute("aria-expanded", "false");
    const stop = button("dl-link", "Остановить");
    more.append(details, stop);
    tools.append(pause, skip, forget, more);
    li.append(pic, body, tools);
    const parts = { li, pic, letter, kind, title, dest, text, bar, fill, sub, logBox, pre, pause, skip, forget, details,
      stop, row: r, log: null, picture: "" };
    pause.addEventListener("click", () => send("/api/downloads/act", { key: parts.row.key,
      act: parts.row.resume ? "resume" : "pause" }, pause));
    skip.addEventListener("click", () => send("/api/downloads/act", { key: parts.row.key, act: "skip" }, skip));
    forget.addEventListener("click", () => send("/api/downloads/act", { key: parts.row.key, act: "forget" }, forget));
    stop.addEventListener("click", () => send("/api/downloads/act", { key: parts.row.key, act: "stop" }, stop));
    details.addEventListener("click", () => toggleLog(parts));
    copyButton.addEventListener("click", async () => {
      const textAll = (pre.textContent || "").replace(/\s+$/, "");
      copied.textContent = !textAll ? "Здесь пока пусто" : (await copy(textAll)) ? "Скопировано" : "Не получилось";
      setTimeout(() => { copied.textContent = ""; }, 6000);
    });
    rows[id] = parts;
    return parts;
  }

  function picture(parts, address, letter) {
    parts.letter.textContent = String(letter || "").trim().charAt(0).toUpperCase();
    if ((address || "") === parts.picture) return;
    if (address && parts.failed && parts.failed.address === address && Date.now() - parts.failed.at < 10000) return;
    parts.picture = address || "";
    parts.pic.querySelectorAll("img").forEach((x) => x.remove());
    parts.pic.classList.remove("has-picture");
    if (!address) return;
    const img = el("img");
    img.alt = "";
    img.decoding = "async";
    img.onload = () => parts.pic.classList.add("has-picture");
    img.onerror = () => {
      img.remove();
      parts.failed = { address, at: Date.now() };
      if (parts.picture === address) parts.picture = "";
    };
    img.src = address;
    parts.pic.append(img);
  }

  function paintJobs(list) {
    const wanted = new Set(list.map(rowId));
    Object.keys(rows).forEach((id) => {
      if (!wanted.has(id)) {
        if (rows[id].log) clearTimeout(rows[id].log.timer);
        rows[id].li.remove();
        delete rows[id];
      }
    });
    list.forEach((r) => {
      const p = jobRow(r);
      p.row = r;
      p.li.dataset.state = r.state;
      p.li.classList.toggle("hid", !!r.hidden);
      p.kind.textContent = r.kind || "";
      p.title.textContent = r.title || "";
      p.title.title = r.title || "";
      p.dest.textContent = r.dest || "";
      p.dest.hidden = !r.dest;
      p.text.textContent = r.text || "";
      p.text.hidden = !r.text;
      p.sub.textContent = r.sub || "";
      p.sub.hidden = !r.sub;
      p.bar.hidden = r.part === null || r.part === undefined;
      p.fill.style.width = Math.max(2, Math.min(100, (Number(r.part) || 0) * 100)).toFixed(1) + "%";
      p.pause.hidden = !r.pause && !r.resume;
      p.pause.textContent = "";
      p.pause.append(svg(r.resume ? ICON.play : ICON.pause, phone ? 16 : 14),
        el("span", "dl-words", r.resume ? "Продолжить" : "Пауза"));
      p.skip.hidden = !r.skip;
      words(p.skip, phone ? "Пропустить" : r.skip || "Пропустить");
      p.skip.title = r.skip || "";
      p.forget.hidden = !r.forget;
      p.stop.hidden = !r.stop || r.state === "stopping";
      p.details.hidden = !r.log;
      if (!r.log && p.log) toggleLog(p, false);
      picture(p, r.hidden ? "" : r.cover, r.hidden ? "" : r.title || r.kind);
      p.pic.classList.toggle("dl-pic-hidden", !!r.hidden);
    });
    const now = [...jobsList.children];
    const order = list.map((r) => rows[rowId(r)].li);
    if (now.length !== order.length || now.some((li, k) => li !== order[k])) jobsList.append(...order);
    goingEmpty.hidden = list.length > 0;
  }

  // «Подробности»: the task's journal, asked while it is open
  function toggleLog(parts, open) {
    const show = open === undefined ? parts.logBox.hidden : open;
    parts.logBox.hidden = !show;
    parts.details.setAttribute("aria-expanded", show ? "true" : "false");
    words(parts.details, show ? "Подробности ▴" : "Подробности ▾");
    if (parts.log) clearTimeout(parts.log.timer);
    if (!show) { parts.log = null; return; }
    parts.log = { after: 0, id: null, timer: 0 };
    parts.pre.textContent = "";
    const mine = parts.log;
    const ask = async () => {
      if (parts.log !== mine) return;
      try {
        const st = await getJson("/api/downloads/log?key=" + encodeURIComponent(parts.row.key) + "&after=" + mine.after);
        if (parts.log !== mine) return;
        if (st.id && st.id !== mine.id) {
          if (mine.id !== null) parts.pre.textContent = "";
          mine.id = st.id;
        }
        const lines = (st.log || []).map((l) => l.text);
        if (lines.length) {
          const stick = parts.pre.scrollTop + parts.pre.clientHeight >= parts.pre.scrollHeight - 30;
          parts.pre.textContent += (parts.pre.textContent ? "\n" : "") + lines.join("\n");
          if (stick) parts.pre.scrollTop = parts.pre.scrollHeight;
        }
        mine.after = Math.max(mine.after, Number(st.last_seq) || 0);
      } catch (_) { /* asked again */ }
      if (parts.log === mine && !document.hidden) mine.timer = setTimeout(ask, 1500);
    };
    ask();
  }

  // ── «Очередь» ───────────────────────────────────────────────────────────────────────────────────────────────────
  function paintQueue(list) {
    queuePanel.hidden = !list.length;
    queuePanel.querySelector(".dl-n").textContent = list.length ? String(list.length) : "";
    queueList.textContent = "";
    list.forEach((q, n) => {
      const li = el("li", "dl-q" + (q.hidden ? " hid" : ""));
      li.dataset.key = q.key;
      li.append(el("span", "dl-qn", String(n + 1)), el("span", "dl-kind", q.kind || ""));
      const main = el("span", "dl-q-main");
      const name = el("span", "dl-q-name");      // the kind beside the name — where its own column is not (narrow)
      name.append(el("span", "dl-kind dl-kind-in", q.kind || ""), el("b", "", q.title || ""));
      main.append(name);
      if (q.what) main.append(el("span", "dl-q-what", (phone ? "" : " — ") + q.what));
      li.append(main);
      const move = el("span", "dl-move");
      [["up", -1, "Выше — начнётся раньше"], ["down", 1, "Ниже — начнётся позже"]].forEach(([icon, step, label]) => {
        const b = el("button", "dl-icon-btn");
        b.type = "button";
        b.append(svg(ICON[icon], 14));
        b.title = label;
        b.setAttribute("aria-label", label);
        b.dataset.move = String(step);
        const off = !(step < 0 ? q.up : q.down);
        b.classList.toggle("dl-off", off);         // its place kept: ↑ and ↓, «убрать» of every line one under another
        b.disabled = off;
        b.addEventListener("click", () => send("/api/downloads/act", { key: q.key, act: "move", id: q.id, step }, b));
        move.append(b);
      });
      move.classList.toggle("dl-off", !q.up && !q.down);
      li.append(move);
      if (q.drop) {
        const drop = button("dl-link", "убрать");
        drop.dataset.drop = "1";
        drop.addEventListener("click", () => send("/api/downloads/act", { key: q.key, act: "drop", id: q.id }, drop));
        li.append(drop);
      }
      queueList.append(li);
    });
  }

  // ── «Готово» ────────────────────────────────────────────────────────────────────────────────────────────────────
  const SIGNS = { ok: "check", part: "warn", fail: "warn", stop: "stop" };

  async function loadDone() {
    let st;
    try { st = await getJson("/api/downloads/done" + (showAll ? "?all=1" : "")); } catch (_) { return; }
    doneList.textContent = "";
    (st.rows || []).forEach((r) => doneList.append(doneRow(r)));
    doneEmpty.hidden = (st.rows || []).length > 0;
    doneAll.hidden = showAll || (st.total || 0) <= (st.rows || []).length;
  }

  function doneRow(r) {
    const li = el("li", "dl-res " + (r.sign || "ok") + (r.hidden ? " hid" : ""));
    li.dataset.id = r.id;
    const sign = el("span", "dl-sign");
    sign.append(svg(ICON[SIGNS[r.sign] || "check"], 14));
    const body = el("div", "dl-res-body");
    const top = el("div", "dl-res-top");
    top.append(el("span", "dl-kind", r.kind || ""), el("b", "", r.title || ""), el("time", "", r.when || ""));
    body.append(top, el("p", "", r.what || ""));
    if ((r.fails || []).length) {
      const ul = el("ul");
      r.fails.forEach((f) => ul.append(el("li", "", f)));
      body.append(ul);
    }
    const tools = el("div", "dl-res-tools");
    if (r.retry && r.retry.href) {
      const a = el("a", "dl-btn");
      a.href = r.retry.href;
      a.append(svg(ICON.retry, 14), el("span", "dl-words", r.retry.label));
      tools.append(a);
    } else if (r.retry) {
      const b = button("dl-btn", r.retry.label, "retry");
      b.dataset.retry = r.id;
      b.addEventListener("click", () => send("/api/downloads/retry", { id: r.id }, b));
      tools.append(b);
    }
    if (r.open) {
      const a = el("a", "dl-link", "Открыть");
      a.href = box.dataset.open + encodeURIComponent(r.id);
      tools.append(a);
    }
    li.append(sign, body, tools);
    return li;
  }

  doneAll.addEventListener("click", () => {
    showAll = true;
    loadDone();
  });

  // ── «Сама программа» ────────────────────────────────────────────────────────────────────────────────────────────
  async function loadDuties() {
    dutiesAt = Date.now();
    let st;
    try { st = await getJson("/api/downloads/duties"); } catch (_) { return; }
    if (dutiesList.contains(document.activeElement)) return;      // a switch being pressed: next time
    dutiesList.textContent = "";
    (st.rows || []).forEach((d) => dutiesList.append(dutyRow(d)));
  }

  function dutyRow(d) {
    const li = el("li", "dl-duty");
    li.dataset.key = d.key;
    li.dataset.state = d.state || "idle";
    const s = d.setting;
    let control = el("span", "dl-sw-none");
    if (s && s.kind === "bool") {
      control = el("input", "switch");
      control.type = "checkbox";
      control.checked = !!s.value;
      control.setAttribute("aria-label", d.name);
      control.addEventListener("change", async () => {
        const reply = await send("/api/downloads/duty", { key: d.key, value: control.checked }, control);
        if (!reply) control.checked = !control.checked;
        dutiesAt = 0;
      });
    } else if (s && s.kind === "choice") {
      control = el("select", "dl-select");
      control.setAttribute("aria-label", d.name);
      (s.choices || []).forEach(([value, label]) => {
        const o = el("option", "", label);
        o.value = String(value);
        o.selected = Number(value) === Number(s.value);
        control.append(o);
      });
      control.addEventListener("change", async () => {
        await send("/api/downloads/duty", { key: d.key, value: Number(control.value) }, control);
        dutiesAt = 0;
      });
    }
    const body = el("div", "dl-duty-body");
    body.append(el("b", "", d.name), el("p", "", d.about || ""));
    const state = el("span", "dl-state");
    state.append(el("span", "dl-dot"), el("span", "", d.status || ""));
    body.append(state);
    if (d.part !== null && d.part !== undefined) {
      const bar = el("span", "dl-bar");
      const fill = el("i");
      fill.style.width = Math.max(2, Math.min(100, Number(d.part) * 100)).toFixed(1) + "%";
      bar.append(fill);
      body.append(bar);
    }
    if (d.action && d.action.href) {
      const a = el("a", "dl-link", d.action.label);
      a.href = d.action.href;
      body.append(a);
    } else if (d.action) {
      const b = button("dl-link", d.action.label);
      b.dataset.dutyAct = d.key;
      b.addEventListener("click", async () => {
        await send("/api/downloads/duty-act", { key: d.key }, b);
        dutiesAt = 0;
      });
      body.append(b);
    }
    li.append(control, body);
    return li;
  }

  // ── «Пауза всего», «Остановить всё» (the second press asks it, as «В корзину») ─────────────────────────────────
  pauseAll.addEventListener("click", () => send("/api/downloads/all", { act: pauseAll.dataset.act || "pause" }, pauseAll));
  stopAll.addEventListener("click", () => {
    if (!stopAll.classList.contains("confirming")) {
      stopAll.classList.add("confirming");
      words(stopAll, "Точно остановить всё и убрать очередь? Нажмите ещё раз");
      clearTimeout(stopAll.timer);
      stopAll.timer = setTimeout(() => {
        stopAll.classList.remove("confirming");
        words(stopAll, "Остановить всё");
      }, 4000);
      return;
    }
    clearTimeout(stopAll.timer);
    stopAll.classList.remove("confirming");
    words(stopAll, "Остановить всё");
    send("/api/downloads/all", { act: "stop" }, stopAll);
  });
  stopAll.textContent = "";
  stopAll.append(svg(ICON.stop, 14), el("span", "dl-words", "Остановить всё"));

  tick();
  loadDuties();
})();
