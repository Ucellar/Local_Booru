// Steam layout of «Загрузки». The shared downloads page owns polling, history and other themes.
(() => {
  "use strict";

  const LB = window.LocalBooru;
  if (!LB) return;
  const endpoint = "/api/downloads/";
  const clock = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  });
  const decimal = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const percent = (part) => Math.floor(clamp(part, 0, 1) * 100);
  const paths = {
    pause: ["M8 5v14", "M16 5v14"], play: ["M7 5v14l12-7z"], stop: ["M6 6h12v12H6z"],
    later: ["M12 8v4l2 2", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"],
    queue: ["M4 6h16", "M4 12h16", "M4 18h16"], close: ["M6 6l12 12", "M18 6L6 18"],
    skip: ["M5 5v14l10-7z", "M19 5v14"],
  };

  function speed(rate) {
    const value = Math.max(0, Number(rate) || 0);
    if (value >= 1e9) return decimal.format(value / 1e9) + " ГБ/с";
    if (value >= 1e6) return decimal.format(value / 1e6) + " МБ/с";
    if (value >= 1e3) return decimal.format(value / 1e3) + " КБ/с";
    return Math.round(value) + " Б/с";
  }

  function vector(tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  }

  function control(icon, label, primary, action) {
    const button = document.createElement("button");
    button.className = "dls-btn " + (primary ? "dls-primary" : "dls-subtle");
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    const svg = vector("svg", { viewBox: "0 0 24 24", width: 17, height: 17, fill: "none",
      stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round",
      "aria-hidden": "true", focusable: "false" });
    svg.append(...paths[icon].map((d) => vector("path", { d })));
    button.append(svg);
    button.addEventListener("click", () => action(button));
    return button;
  }

  function cover(node, row) {
    const address = !row?.hidden && typeof row?.cover === "string" && /^\/api\/[a-z/\-]+\?/.test(row.cover)
      ? row.cover : "";
    if (node.dataset.address === address && (!address || !node.failedAt || Date.now() - node.failedAt < 15000)) return;
    node.dataset.address = address;
    node.failedAt = 0;
    node.querySelector("img")?.remove();
    if (!address) return;
    const image = new Image();
    image.alt = "";
    image.draggable = false;
    image.decoding = "async";
    image.addEventListener("error", () => {
      if (image.isConnected) node.failedAt = Date.now();
      image.remove();
    }, { once: true });
    image.src = address;
    node.append(image);
  }

  function renderChart(chart, isDragging) {
    const graph = chart.querySelector("svg");
    const tip = chart.querySelector("[data-dl-tip]");
    const label = tip.querySelector("strong");
    const time = tip.querySelector("time");
    const [net, disk] = tip.querySelectorAll("b");
    const dot = chart.querySelector(".dls-dot");
    const length = 600;
    const height = 100;
    const guide = vector("line", { class: "dls-guide", y1: 0, y2: height });
    let signature = "";
    let samples = [];
    let bars = [];
    let selection = null;
    let stride = 0;
    let offset = 0;
    let scale = 1;
    const x = (index) => offset + (index + .5) * stride;
    const y = (value) => height * (1 - value / scale);

    function select(index) {
      bars.forEach((bar) => bar.classList.remove("dls-selected"));
      const chosen = !isDragging() && samples[index];
      if (!chosen) {
        selection = null;
        tip.hidden = dot.hidden = true;
        guide.remove();
        chart.removeAttribute("aria-describedby");
        return;
      }
      selection = chosen.at;
      const point = x(index) / length;
      bars[index].classList.add("dls-selected");
      guide.setAttribute("x1", x(index));
      guide.setAttribute("x2", x(index));
      graph.append(guide);
      dot.style.left = point * 100 + "%";
      dot.style.top = y(chosen.disk) + "%";
      tip.style.setProperty("--dls-tip-x", point * 100 + "%");
      tip.classList.toggle("dls-flip", point > .64);
      time.dateTime = new Date(chosen.at).toISOString();
      time.textContent = clock.format(chosen.at);
      net.textContent = chosen.net_unknown ? "Нет замера" : speed(chosen.net);
      disk.textContent = speed(chosen.disk);
      tip.hidden = dot.hidden = false;
      chart.setAttribute("aria-describedby", tip.id);
    }

    chart.addEventListener("pointermove", (event) => {
      const rect = chart.getBoundingClientRect();
      const mark = ((event.clientX - rect.left) / rect.width * length - offset) / stride;
      const index = Math.floor(mark);
      if ((samples[index]?.at ?? null) !== selection) select(index);
    });
    chart.addEventListener("pointerleave", () => select(-1));
    chart.addEventListener("blur", () => select(-1));
    chart.addEventListener("keydown", (event) => {
      if (event.key === "Escape") return select(-1);
      if (!samples.length) return;
      const at = samples.findIndex((sample) => sample.at === selection);
      const last = samples.length - 1;
      const previous = at < 0 ? last : at;
      const keys = { Home: 0, End: last, ArrowLeft: previous - 1, ArrowRight: previous + 1 };
      if (!Object.hasOwn(keys, event.key)) return;
      event.preventDefault();
      select(clamp(keys[event.key], 0, last));
    });

    return (values = [], title = "") => {
      label.textContent = title;
      label.hidden = !title;
      const stamp = JSON.stringify(values);
      if (stamp === signature) {
        if (isDragging() && selection !== null) select(-1);
        return;
      }
      signature = stamp;
      samples = values.filter((sample) => Number.isFinite(sample.at)).slice(-120);
      chart.setAttribute("aria-disabled", String(!samples.length));
      chart.tabIndex = samples.length ? 0 : -1;
      scale = 1.12 * Math.max(1, ...samples.map(({ net, disk }) => Math.max(net, disk)));
      stride = length / Math.max(samples.length, 48);
      offset = length - stride * samples.length;
      bars = samples.map(({ net, net_unknown: unknown }, index) => vector("rect", {
        class: "dls-bar", x: x(index) - stride * .35, y: y(net), width: stride * .7, height: unknown ? 0 : height - y(net),
      }));
      const grid = [.25, .5, .75].map((level) => vector("line", { class: "dls-grid", x1: 0, x2: length,
        y1: height * level, y2: height * level }));
      const line = vector("polyline", { class: "dls-line",
        points: samples.map(({ disk }, index) => x(index) + "," + y(disk)).join(" ") });
      graph.replaceChildren(...grid, ...bars, line);
      select(samples.findIndex((sample) => sample.at === selection));
    };
  }

  LB.createSteamDownloads = (board, send) => {
    const active = board.querySelector('[data-dl-lane="active"]');
    const lanes = [...board.querySelectorAll(".dls-lane")];
    const title = active.querySelector(".dls-title h2");
    const status = active.querySelector(".dls-status span");
    const art = active.querySelector(".dls-art");
    const rates = active.querySelectorAll("dd");
    const work = active.querySelector(".dls-work");
    const meter = active.querySelector(".dls-meter");
    const track = meter.querySelector(".dls-track");
    const processing = active.querySelector(".dls-processing");
    const workDetail = active.querySelector(".dls-work-detail");
    const actions = active.querySelector(".dls-active-actions");
    const dropHint = active.querySelector(".dls-drop-hint");
    const warning = board.querySelector(".dls-warning");
    const rows = new Map();
    let state = null;
    let dragging = null;
    let dragTarget = null;
    let onControl = false;
    let activeControls = "";
    let pending = false;
    const chart = renderChart(active.querySelector("[data-dl-chart]"), () => Boolean(dragging));

    async function mutate(route, data, button) {
      if (pending) return;
      pending = true;
      try { await send(endpoint + route, data, button); }
      finally { pending = false; }
    }

    function move(id, lane, before = null, button, revision = state?.revision) {
      if (id && state && before !== id) return mutate("steam-move", { id, lane, before, revision }, button);
    }

    function drawActive(data) {
      const row = data.active;
      const current = row?.state || "";
      const statusText = { paused: "На паузе", stopping: "Останавливается", waiting: "Ждёт ответа", starting: "Запускается" };
      active.dataset.state = current;
      active.dataset.dlId = row?.id || "";
      active.draggable = Boolean(row?.id && current !== "stopping");
      status.textContent = data.switching ? "Переключаю загрузку…" : row ?
        (statusText[current] || (row.processing_only ? "Обрабатываю файлы" : "Загружается")) :
        data.nativeBusy ? "Завершаются предыдущие загрузки" : "Нет активной загрузки";
      title.textContent = row?.current_title || row?.title || "";
      cover(art, row);
      work.hidden = !row;
      meter.hidden = row?.part == null;
      processing.hidden = !row?.processing;
      processing.querySelector("strong").textContent = row?.processing_title || "";
      if (row?.part != null) {
        const value = percent(row.part);
        meter.querySelector("strong").textContent = value + "%";
        track.setAttribute("aria-valuenow", String(value));
        track.firstElementChild.style.width = value + "%";
      }
      const words = row?.processing_only ? "" : (row?.transfer ?? row?.text ?? "");
      const detail = words.split(" · ").filter((piece) => piece !== row?.speed);
      workDetail.textContent = [...detail, row?.sub].filter(Boolean).join(" · ");
      const controls = JSON.stringify(row ? [row.id, row.key, row.task, row.pause, row.resume, row.skip, row.stop,
        row.can_defer, current === "stopping"] : []);
      if (controls !== activeControls) {
        activeControls = controls;
        const buttons = [];
        if (row?.can_defer !== false && row?.id && current !== "stopping") buttons.push(control("later", "Отложить", false,
          (button) => move(row.id, "later", null, button)));
        if (row?.skip) buttons.push(control("skip", row.skip, false,
          (button) => send(endpoint + "act", { key: row.key, act: "skip", task: row.task }, button)));
        if (row?.stop && current !== "stopping") buttons.push(control("stop", "Остановить", false,
          (button) => mutate("steam-remove", { id: row.id }, button)));
        if (row?.pause || row?.resume) buttons.push(control(row.resume ? "play" : "pause", row.resume ? "Продолжить" : "Пауза", true,
          (button) => send(endpoint + "act", { key: row.key, act: row.resume ? "resume" : "pause", task: row.task }, button)));
        actions.replaceChildren(...buttons);
      }
      const traffic = data.traffic || {};
      [traffic.net, traffic.max_net, traffic.disk, traffic.max_disk].forEach((rate, index) => {
        const have = traffic.samples?.length || rate > 0;
        const unknown = (index === 0 && traffic.net_unknown) || (index === 1 && !rate &&
          traffic.samples?.every((sample) => sample.net_unknown));
        rates[index].textContent = have && !unknown ? speed(rate) : "—";
      });
      chart(traffic.samples, row?.title || "");
    }

    function drawLane(section, list) {
      const lane = section.dataset.dlLane;
      const target = section.querySelector("ol");
      const wanted = list.map((row) => {
        const signature = JSON.stringify([lane, row]);
        const existing = rows.get(row.id);
        if (existing?.signature === signature) {
          cover(existing.node.querySelector(".dls-thumb"), row);
          return existing.node;
        }
        const node = document.createElement("li");
        node.className = "dls-row";
        node.dataset.dlId = row.id;
        node.draggable = true;
        node.tabIndex = 0;
        const picture = document.createElement("div");
        picture.className = "dls-thumb";
        picture.setAttribute("aria-hidden", "true");
        picture.textContent = row.hidden ? "18+" : (row.kind || row.title || "").trim().slice(0, 1);
        cover(picture, row);
        const name = document.createElement("div");
        const heading = document.createElement("h3");
        heading.textContent = row.title || "Загрузка";
        const description = document.createElement("p");
        description.textContent = [row.kind, row.sub, row.can_defer === false ? "Можно скачать сейчас или оставить в очереди" : ""]
          .filter(Boolean).join(" · ");
        name.append(heading, description);
        const progress = document.createElement("div");
        progress.className = "dls-progress";
        progress.textContent = row.part == null ? "" : percent(row.part) + "%";
        const buttons = document.createElement("div");
        buttons.className = "dls-actions";
        buttons.append(control("play", "Загрузить сейчас", true, (button) => move(row.id, "active", null, button)));
        if (lane === "next" && row.can_defer !== false) buttons.append(control("later", "Отложить", false,
          (button) => move(row.id, "later", null, button)));
        if (lane === "later") buttons.append(control("queue", "В очередь", false,
          (button) => move(row.id, "next", null, button)));
        buttons.append(control("close", "Убрать", false, (button) => mutate("steam-remove", { id: row.id }, button)));
        node.append(picture, name, progress, buttons);
        rows.set(row.id, { signature, node });
        return node;
      });
      const current = [...target.children];
      if (wanted.length !== current.length || wanted.some((node, index) => node !== current[index])) {
        let at = 0;
        for (const node of wanted) {
          if (target.children[at] !== node) target.insertBefore(node, target.children[at] || null);
          at++;
        }
        while (target.children.length > at) target.lastElementChild.remove();
      }
      section.querySelector("[data-dl-lane-count]").textContent = "(" + list.length + ")";
      section.querySelector(".dls-empty").hidden = list.length !== 0;
    }

    function paint(data) {
      state = data;
      if (dragging) return;
      board.hidden = !data;
      if (!data) return;
      const focused = document.activeElement;
      const focusedRow = board.contains(focused) && focused.closest("[data-dl-id]")?.dataset.dlId;
      const error = data.error || "";
      if (warning.textContent !== error) warning.textContent = error;
      warning.hidden = !data.error;
      drawActive(data);
      lanes.forEach((section) => drawLane(section, data[section.dataset.dlLane] || []));
      const known = new Set([...data.next, ...data.later].map((row) => row.id));
      for (const id of rows.keys()) if (!known.has(id)) rows.delete(id);
      if (focusedRow && document.activeElement !== focused) {
        const target = focusedRow === data.active?.id ? active : rows.get(focusedRow)?.node;
        target?.focus({ preventScroll: true });
      }
    }

    function showDrop(target) {
      if (dragTarget?.node === target?.node && dragTarget?.before === target?.before) return;
      dragTarget?.node.classList.remove("dls-target", "dls-before", "dls-end");
      dragTarget = target;
      dropHint.hidden = target?.lane !== "active";
      if (!target) return;
      target.node.classList.add(target.lane === "active" ? "dls-target" : target.before ? "dls-before" : "dls-end");
    }

    function endDrag(repaint = true) {
      if (!dragging) return;
      dragging.node.classList.remove("dls-dragging");
      dragging = null;
      onControl = false;
      showDrop(null);
      if (repaint) paint(state);
    }

    board.addEventListener("pointerdown", (event) => { onControl = Boolean(event.target.closest("button, [data-dl-chart]")); });
    board.addEventListener("dragstart", (event) => {
      const node = event.target.closest("[data-dl-id]");
      if (!node?.dataset.dlId || onControl || pending) { event.preventDefault(); return; }
      const id = node.dataset.dlId;
      const item = [state.active, ...state.next, ...state.later].find((row) => row?.id === id);
      dragging = { id, node, revision: state.revision, canDefer: item?.can_defer !== false };
      event.dataTransfer.setData("text/plain", dragging.id);
      event.dataTransfer.effectAllowed = "move";
      node.classList.add("dls-dragging");
      chart(state.traffic?.samples, state.active?.title || "");
    });

    function destination(section, event) {
      const lane = section.dataset.dlLane;
      if (lane === "active") return dragging.node === active ? null : { node: active, lane, before: null };
      if (lane === "later" && !dragging.canDefer) return null;
      const candidates = [...section.querySelectorAll(".dls-row")].filter((row) => row !== dragging.node);
      const next = candidates.find((node) => {
        const bounds = node.getBoundingClientRect();
        return event.clientY < bounds.top + bounds.height / 2;
      });
      return { node: next || section, lane, before: next?.dataset.dlId || null };
    }

    board.addEventListener("dragover", (event) => {
      const section = event.target.closest("[data-dl-lane]");
      if (!dragging || !section) return;
      const target = destination(section, event);
      showDrop(target);
      if (!target) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
    });
    board.addEventListener("dragleave", (event) => {
      const section = event.target.closest("[data-dl-lane]");
      if (section && !section.contains(event.relatedTarget)) showDrop(null);
    });
    board.addEventListener("drop", (event) => {
      const section = event.target.closest("[data-dl-lane]");
      if (!dragging || !section) return;
      const target = destination(section, event);
      if (!target) return;
      event.preventDefault();
      move(dragging.id, target.lane, target.before, undefined, dragging.revision);
      endDrag();
    });
    board.addEventListener("dragend", () => endDrag());
    board.addEventListener("keydown", (event) => {
      if (!event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key) || !event.target.matches(".dls-row")) return;
      const row = event.target;
      const lane = row.closest("[data-dl-lane]").dataset.dlLane;
      const list = state[lane];
      const at = list.findIndex((item) => item.id === row.dataset.dlId);
      const next = at + (event.key === "ArrowUp" ? -1 : 1);
      if (next < 0 || next >= list.length) return;
      event.preventDefault();
      move(row.dataset.dlId, lane, list[next < at ? next : next + 1]?.id || null);
    });

    return { paint, endDrag };
  };
})();
