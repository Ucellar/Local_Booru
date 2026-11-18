// Local Booru «Скачать → Загрузки» (v5.44.120, webui/pages/downloads.py): everything that downloads in one place —
// the manga and manhwa of the sites, nhentai, the videos, «Галерея (booru)» and the subscriptions; one queue with its
// places, drag and drop and ↑↓; «Готово» for 7 days with «Повторить не скачавшееся»; «Сама программа» with its switches. The
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
  const queueHelp = queuePanel.querySelector(".dl-queue-help");
  const donePanel = box.querySelector(".dl-done-panel");
  const doneList = donePanel.querySelector(".dl-done");
  const doneEmpty = donePanel.querySelector(".dl-done-empty");
  const doneAll = donePanel.querySelector("[data-done-all]");
  const dutiesList = box.querySelector(".dl-duties");
  const steamBoard = box.querySelector("[data-steam-downloads]");
  const steam = steamBoard && LB.createSteamDownloads?.(steamBoard, send);
  const steamSelected = () => steam && document.documentElement.dataset.skin === "steam";
  const STATES = { running: "идёт", paused: "пауза", stopping: "останавливается", waiting: "ждёт ответа",
    restoring: "с прошлого запуска" };
  const ICON = {
    pause: ["M8 5v14", "M16 5v14"], play: ["M7 4.5v15l12-7.5z"], skip: ["M5 5v14l10-7z", "M19 5v14"],
    stop: ["M6 6h12v12H6z"], retry: ["M4 12a8 8 0 1 0 2.4-5.7", "M4 4v4.6h4.6"], check: ["M5 12.5l4.5 4.5L19 7.5"],
    warn: ["M12 4l9 16H3z", "M12 10v4.5", "M12 17.5h.01"], up: ["M12 19V5", "M6 11l6-6 6 6"],
    down: ["M12 5v14", "M6 13l6 6 6-6"], list: ["M9 6h11", "M9 12h11", "M9 18h11", "M4.5 6h.01", "M4.5 12h.01",
      "M4.5 18h.01"], close: ["M6 6l12 12", "M18 6L6 18"],
    grip: ["M9 5h.01", "M15 5h.01", "M9 12h.01", "M15 12h.01", "M9 19h.01", "M15 19h.01"],
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

  // ── (v5.44.158) «Плавные переходы»: a row that changed its place moves there, a new one comes in ────────────────
  // (the mockup «Анимации»; booru.css sets data-motion and the lengths; nothing of it on the phone or with the
  // transitions off).  The lists are drawn anew as the state comes: where each row stood is noted before, and after —
  // a row known before is moved from its old place to the new one, a row not seen before comes in (.dl-new).
  const root = document.documentElement;
  const moves = () => root.dataset.motion === "on" && typeof Element.prototype.animate === "function";
  function places(list) {
    const at = new Map();
    if (moves()) for (const li of list.children) at.set(li.dataset.row || "", li.getBoundingClientRect().top);
    return at;
  }
  function settle(list, before) {
    if (!moves()) return;
    const style = getComputedStyle(root);
    const duration = parseFloat(style.getPropertyValue("--m-page")) || 220;
    const easing = style.getPropertyValue("--m-ease").trim() || "ease-out";
    let fresh = 0;
    for (const li of list.children) {
      const row = li.dataset.row || "";
      if (before.has(row)) {
        const by = before.get(row) - li.getBoundingClientRect().top;
        if (Math.abs(by) > 2) li.animate([{ transform: "translateY(" + by + "px)" }, { transform: "none" }], { duration, easing });
      } else {
        li.style.setProperty("--m-i", String(Math.min(fresh++, 16)));
        li.classList.add("dl-new");                    // once: a row put into the list again must not come in again
        li.addEventListener("animationend", () => li.classList.remove("dl-new"), { once: true });
      }
    }
  }

  // ── the state: asked while the page is seen ─────────────────────────────────────────────────────────────────────
  let timer = 0;
  let asking = false;
  let doneRev = null;
  let dutiesAt = 0;
  let showAll = false;
  let queueRev = null;
  let lastSfw = box.dataset.sfw === "1";

  async function tick() {
    clearTimeout(timer);
    if (document.hidden || asking) return;
    asking = true;
    try {
      const st = await getJson("/api/downloads/state" + (steamSelected() ? "?steam=1" : ""));
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
    if (document.hidden) {
      endDrag();
      steam?.endDrag();
      clearTimeout(timer);
    } else tick();
  });
  if (steam) new MutationObserver(() => {
    steam.endDrag();
    if (!steamSelected()) steam.paint(null);
    poke();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-skin"] });

  function paint(st) {
    queueRev = st.queue_rev;
    if (lastSfw !== Boolean(st.sfw)) {
      steam?.endDrag(false);
      lastSfw = Boolean(st.sfw);
    }
    const managed = steamSelected() && st.steam;
    steam?.paint(managed || null);
    const visibleJobs = (st.jobs || []).filter((row) => {
      if (managed?.handoverTask && row.task === managed.handoverTask) return false;
      return !managed?.active || (row.id !== managed.active.id &&
        !(row.key === managed.active.key && row.task && row.task === managed.active.task));
    });
    const visibleQueue = (st.queue || []).filter((row) => !managed || !row.scheduled);
    going.hidden = Boolean(managed && !visibleJobs.length);
    going.querySelector("h2").firstChild.textContent = managed ? "Другие задачи " : "Идёт сейчас ";
    countLine.textContent = st.said || "";
    going.querySelector(".dl-n").textContent = String(visibleJobs.filter((r) => r.state !== "restoring").length || "");
    const any = (st.jobs || []).some((r) => r.state !== "restoring") || (st.queue || []).some((r) => r.lane !== "later");
    headTools.hidden = !any;
    const pausable = (st.jobs || []).filter((r) => r.pause);
    const resumable = (st.jobs || []).filter((r) => r.resume);
    pauseAll.hidden = !pausable.length && !resumable.length;
    pauseAll.dataset.act = pausable.length ? "pause" : "resume";
    pauseAll.textContent = "";
    pauseAll.append(svg(pausable.length ? ICON.pause : ICON.play, 15),
      el("span", "dl-words", pausable.length ? "Пауза всего" : "Продолжить всё"));
    paintJobs(visibleJobs);
    paintQueue(visibleQueue);
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
    li.dataset.row = id;
    const pic = el("span", "dl-pic");
    const letter = el("span", "dl-letter");
    letter.setAttribute("aria-hidden", "true");
    pic.append(letter, svg(ICON.list, 22));
    const body = el("div", "dl-body");
    const top = el("div", "dl-top");
    const kind = el("span", "dl-kind");
    const status = el("span", "dl-status");
    const title = el("b", "dl-title");
    const dest = el("span", "dl-dest");
    top.append(kind, status, title, dest);
    const text = el("p", "dl-text");
    const bar = el("span", "dl-bar");
    const fill = el("i");
    bar.append(fill);
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "Ход загрузки");
    const percent = el("span", "dl-percent");
    percent.setAttribute("aria-hidden", "true");
    const progress = el("div", "dl-job-progress");
    progress.append(bar, percent);
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
    body.append(top, text, progress, sub, logBox);
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
    const parts = { li, pic, letter, kind, status, title, dest, text, progress, bar, fill, percent, sub, logBox, pre, pause, skip, forget, details,
      stop, row: r, log: null, picture: "" };
    pause.addEventListener("click", () => send("/api/downloads/act", { key: parts.row.key,
      act: parts.row.resume ? "resume" : "pause" }, pause));
    skip.addEventListener("click", () => send("/api/downloads/act", { key: parts.row.key, act: "skip" }, skip));
    forget.addEventListener("click", () => send("/api/downloads/act", { key: parts.row.key, act: "forget" }, forget));
    stop.addEventListener("click", () => parts.row.scheduled ? send("/api/downloads/steam-remove", { id: parts.row.id }, stop) :
      send("/api/downloads/act", { key: parts.row.key, act: "stop" }, stop));
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
    img.onload = () => {
      if (parts.picture === address && img.isConnected) parts.pic.classList.add("has-picture");
    };
    img.onerror = () => {
      img.remove();
      parts.failed = { address, at: Date.now() };
      if (parts.picture === address) parts.picture = "";
    };
    img.src = address;
    parts.pic.append(img);
  }

  function paintJobs(list) {
    const before = places(jobsList);
    const primary = list.find((r) => r.state === "running") || list.find((r) => r.state !== "restoring");
    if (primary && primary !== list[0]) list = [primary, ...list.filter((r) => r !== primary)];
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
      p.li.classList.toggle("dl-feature", r === primary);
      p.kind.textContent = r.kind || "";
      p.status.textContent = STATES[r.state] || "";
      p.status.hidden = !p.status.textContent;
      p.title.textContent = r.current_title || r.title || "";
      p.title.title = p.title.textContent;
      p.dest.textContent = r.dest || "";
      p.dest.hidden = !r.dest;
      p.text.textContent = r.text || "";
      p.text.hidden = !r.text;
      p.sub.textContent = r.sub || "";
      p.sub.hidden = !r.sub;
      p.progress.hidden = r.part === null || r.part === undefined;
      const pct = Math.max(0, Math.min(100, (Number(r.part) || 0) * 100));
      p.fill.style.width = pct.toFixed(1) + "%";
      p.bar.setAttribute("aria-valuenow", String(Math.round(pct)));
      p.percent.textContent = Math.floor(pct) + "%";
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
      picture(p, r.hidden ? "" : r.cover, r.hidden ? "" : p.title.textContent || r.kind);
      p.pic.classList.toggle("dl-pic-hidden", !!r.hidden);
    });
    const now = [...jobsList.children];
    const order = list.map((r) => rows[rowId(r)].li);
    if (now.length !== order.length || now.some((li, k) => li !== order[k])) jobsList.append(...order);
    goingEmpty.hidden = list.length > 0;
    settle(jobsList, before);
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
  let queueSaid = "";             // (v5.44.158) the queue as it was drawn last
  let queueRows = [];
  let dragging = null;
  let settlingOrder = false;

  function inQueue(key) {
    return queueRows.filter((q) => q.key === key && !q.scheduled).map((q) => q.id);
  }

  async function sendOrder(key, from, to, expected) {
    if (from === to || settlingOrder || from < 0 || to < 0 || to >= expected.length) return;
    const order = expected.slice();
    const [moved] = order.splice(from, 1);
    order.splice(to, 0, moved);
    settlingOrder = true;
    try {
      await send("/api/downloads/act", { key, act: "reorder", expected, order });
    } finally {
      settlingOrder = false;
    }
  }

  function endDrag() {
    if (!dragging) return;
    dragging.row.classList.remove("dl-dragging");
    if (dragging.over) delete dragging.over.dataset.insert;
    dragging = null;
    poke();
  }

  queueList.addEventListener("dragstart", (event) => {
    const handle = event.target.closest(".dl-grip");
    if (!handle || settlingOrder || !event.dataTransfer) return;
    const row = handle.closest(".dl-q");
    const expected = inQueue(row.dataset.key);
    if (expected.length < 2) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData("text/plain", "download");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setDragImage(row, 24, row.offsetHeight / 2);
    dragging = { row, expected, from: expected.indexOf(Number(row.dataset.id)), over: null };
    row.classList.add("dl-dragging");
  });

  function dropPosition(event) {
    const row = event.target.closest(".dl-q");
    if (!dragging || !row || row.dataset.key !== dragging.row.dataset.key || row === dragging.row) return null;
    const bounds = row.getBoundingClientRect();
    const after = event.clientY > bounds.top + bounds.height / 2;
    const index = dragging.expected.indexOf(Number(row.dataset.id));
    if (index < 0) return null;
    const slot = index + Number(after);
    return { row, after, to: slot - Number(dragging.from < slot) };
  }

  queueList.addEventListener("dragover", (event) => {
    if (!dragging) return;
    const target = dropPosition(event);
    if (dragging.over) delete dragging.over.dataset.insert;
    dragging.over = target ? target.row : null;
    if (!target) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    target.row.dataset.insert = target.after ? "after" : "before";
  });
  queueList.addEventListener("dragleave", (event) => {
    if (dragging && dragging.over && (!(event.relatedTarget instanceof Node) || !queueList.contains(event.relatedTarget))) {
      delete dragging.over.dataset.insert;
      dragging.over = null;
    }
  });
  queueList.addEventListener("drop", (event) => {
    const target = dropPosition(event);
    if (!target) return;
    event.preventDefault();
    const { row, from, expected } = dragging;
    endDrag();
    sendOrder(row.dataset.key, from, target.to, expected);
  });
  box.addEventListener("dragend", endDrag);

  function paintQueue(list) {
    // A poll may arrive during a drag. Keep its source and targets until drop, then check the snapshot on the server.
    if (dragging || settlingOrder) return;
    queueRows = list;
    queuePanel.hidden = !list.length;
    queuePanel.querySelector(".dl-n").textContent = list.length ? String(list.length) : "";
    queueHelp.hidden = !list.some((q) => q.up || q.down);
    // nothing new: the rows stay as they are (the button under the pointer, the one in focus) — drawn anew every
    // 1.5 s they lost both
    const said = JSON.stringify(list);
    if (said === queueSaid && queueList.children.length === list.length) return;
    queueSaid = said;
    const before = places(queueList);
    const focused = document.activeElement;
    const focusedRow = queueList.contains(focused) ? focused.closest(".dl-q") : null;
    queueList.textContent = "";
    list.forEach((q, n) => {
      const li = el("li", "dl-q" + (q.hidden ? " hid" : ""));
      li.dataset.key = q.key;
      li.dataset.id = String(q.id);
      li.dataset.row = q.key + "|" + q.id;
      if (n > 0 && list[n - 1].key !== q.key) li.dataset.kindStart = "1";
      li.append(el("span", "dl-qn", String(n + 1)), el("span", "dl-kind", q.kind || ""));
      const main = el("span", "dl-q-main");
      const name = el("span", "dl-q-name");      // the kind beside the name — where its own column is not (narrow)
      name.append(el("span", "dl-kind dl-kind-in", q.kind || ""), el("b", "", q.title || ""));
      main.append(name);
      if (q.what) main.append(el("span", "dl-q-what", (phone ? "" : " — ") + q.what));
      li.append(main);
      if (!phone) {
        const handle = el("span", "dl-grip");
        handle.append(svg(ICON.grip, 16));
        handle.classList.toggle("dl-off", q.scheduled || !q.up && !q.down);
        handle.draggable = !q.scheduled && !!(q.up || q.down);
        handle.title = "Перетащить в очереди";
        handle.setAttribute("aria-hidden", "true");
        li.prepend(handle);
      }
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
        b.addEventListener("click", () => {
          if (settlingOrder) return;
          if (q.scheduled) {
            const entries = queueRows.filter((row) => row.scheduled && row.lane === q.lane);
            const at = entries.findIndex((row) => row.id === q.id);
            send("/api/downloads/steam-move", { id: q.id, lane: q.lane, revision: queueRev,
              before: entries[at + (step < 0 ? -1 : 2)]?.id || null }, b);
          } else if (q.key === "video") {
            const expected = inQueue(q.key);
            const from = expected.indexOf(q.id);
            sendOrder(q.key, from, from + step, expected);
          } else send("/api/downloads/act", { key: q.key, act: "move", id: q.id, step }, b);
        });
        move.append(b);
      });
      move.classList.toggle("dl-off", !q.up && !q.down);
      li.append(move);
      if (q.drop) {
        const drop = button("dl-link", "убрать");
        drop.dataset.drop = "1";
        drop.addEventListener("click", () => q.scheduled ? send("/api/downloads/steam-remove", { id: q.id }, drop) :
          send("/api/downloads/act", { key: q.key, act: "drop", id: q.id }, drop));
        li.append(drop);
      }
      queueList.append(li);
      if (focusedRow && li.dataset.row === focusedRow.dataset.row) {
        const matching = [...li.querySelectorAll("button")].find((b) => b.dataset.move === focused.dataset.move
          && b.dataset.drop === focused.dataset.drop && !b.disabled);
        const next = matching || li.querySelector("button:not(:disabled)");
        if (next) next.focus({ preventScroll: true });
      }
    });
    settle(queueList, before);
  }

  // ── «Готово» ────────────────────────────────────────────────────────────────────────────────────────────────────
  const SIGNS = { ok: "check", part: "warn", fail: "warn", stop: "stop" };
  const openEarlier = new Set();      // the titles whose earlier downloads are unfolded (kept while the list is drawn anew)

  async function loadDone() {
    let st;
    try { st = await getJson("/api/downloads/done" + (showAll ? "?all=1" : "")); } catch (_) { return; }
    const before = places(doneList);
    doneList.textContent = "";
    (st.rows || []).forEach((r) => doneList.append(doneRow(r)));
    settle(doneList, before);
    doneEmpty.hidden = (st.rows || []).length > 0;
    doneAll.hidden = showAll || (st.total || 0) <= (st.rows || []).length;
  }

  function doneRow(r) {
    const li = el("li", "dl-res " + (r.sign || "ok") + (r.hidden ? " hid" : ""));
    li.dataset.id = r.id;
    li.dataset.row = String(r.id);
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
    if (r.note) body.append(el("p", "dl-note", r.note));   // (v5.44.146) where the chapters were looked for
    // (v5.44.151) the title's downloads before this one — folded: a title is one row, not a row a download
    if (r.earlier && (r.earlier.lines || []).length) {
      const fold = el("details", "dl-earlier");
      fold.open = openEarlier.has(r.key);
      fold.addEventListener("toggle", () => { if (fold.open) openEarlier.add(r.key); else openEarlier.delete(r.key); });
      fold.append(el("summary", "", r.earlier.words || "Раньше"));
      const ol = el("ol");
      r.earlier.lines.forEach((line) => {
        const item = el("li", line.sign || "");
        if (line.when) item.append(el("time", "", line.when));
        item.append(el("span", "", line.what || ""));
        ol.append(item);
      });
      fold.append(ol);
      body.append(fold);
    }
    const tools = el("div", "dl-res-tools");
    if (r.retry) {
      const b = button("dl-btn", r.retry.label, "retry");
      b.dataset.retry = r.id;
      b.addEventListener("click", async () => {
        const reply = await send("/api/downloads/retry", { id: r.id, phone }, b);
        if (reply && reply.href) window.location.assign(reply.href);
      });
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
