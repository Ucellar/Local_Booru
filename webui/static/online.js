// (v5.44.82) A chapter read from a site without downloading it (webui/pages/manga_online.py, core/manga_online.py).
// The way it is read — the switch «Авто / Лента / Справа налево / Слева направо» (the title's own choice, kept by the
// program); the strip is static/manhwa.js.  The pages one at a time — as the friend's reader Yomi has them: one page
// in the window; a click on its right or left third turns it (for manga, right to left — the left third goes on), the
// middle shows and hides the bars; ← → turn it the same way, Space / PageDown — on, PageUp — back; a swipe turns it on
// a phone; a double click zooms in where it was made (then the page is dragged; a double click again — back); the
// slider jumps; the next four pages are asked ahead; after the last page — «Глава закончилась» and the next chapter.
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const actions = LB.actions || {};
  const postJson = LB.postJson || ((url, body) => fetch(url, { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));

  // ── the way it is read (both ways) ──
  actions["ol-mode"] = async (button) => {
    const root = document.querySelector("[data-mode-api]");
    if (!root) return;
    try {
      await postJson(root.dataset.modeApi, { mode: button.dataset.mode || "" });
      location.reload();
    } catch (err) {
      if (LB.say) LB.say(button, err.message, true);
    }
  };

  const reader = document.querySelector(".ol-reader");
  if (!reader) return;
  const pages = JSON.parse(reader.dataset.pages || "[]");
  const total = pages.length;
  const rtl = reader.dataset.mode === "rtl";
  const stage = reader.querySelector(".ol-stage");
  const img = stage.querySelector(".ol-page");
  const end = reader.querySelector(".ol-end");
  const slider = reader.querySelector(".ol-slider");
  const at = reader.querySelector(".ol-at");
  const problem = reader.querySelector(".ol-problem");
  const bar = reader.querySelector("[data-bar]");
  const list = reader.querySelector(".mh-list");
  const where = reader.querySelector(".mh-bar-where");
  const AHEAD = 4;                     // pages asked for ahead
  const kept = new Map();              // page → its Image asked ahead (kept while near)
  let n = Math.max(0, Math.min(Number(reader.dataset.page) || 0, Math.max(0, total - 1)));
  let saveTimer = 0;
  let zoom = { on: false, x: 0, y: 0, scale: 2.5, dragging: false, from: null };

  function ahead() {
    for (let k = n + 1; k <= Math.min(total - 1, n + AHEAD); k++) {
      if (kept.has(k)) continue;
      const pic = new Image();
      pic.decoding = "async";
      pic.src = pages[k].src;
      kept.set(k, pic);
    }
    for (const k of [...kept.keys()]) if (k < n - 2 || k > n + AHEAD) kept.delete(k);
  }

  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      postJson(reader.dataset.progress, { chapter: reader.dataset.chapter, page: Math.min(n, total - 1),
        part: 0, done: n >= total - 1 }).catch(() => {});
    }, 700);
  }

  function unzoom() {
    zoom.on = false;
    img.style.transform = "";
    stage.classList.remove("zoomed");
  }

  function show(k) {
    k = Math.max(0, Math.min(k, total));               // ``total`` — the end of the chapter
    const atEnd = k >= total;
    n = Math.min(k, Math.max(0, total - 1));
    unzoom();
    end.hidden = !atEnd;
    stage.hidden = atEnd;
    if (!atEnd) {
      problem.hidden = true;
      img.classList.add("loading");
      img.src = pages[k].src;
      img.alt = `Страница ${k + 1} из ${total}`;
      if (pages[k].w > 0 && pages[k].h > 0) { img.width = pages[k].w; img.height = pages[k].h; }
      ahead();
    }
    slider.value = String(Math.min(k, total - 1) + 1);
    at.textContent = atEnd ? "конец" : String(k + 1);
    const address = new URL(location.href);
    address.searchParams.set("p", String(Math.min(k, total - 1) + 1));
    history.replaceState(history.state, "", address);
    save();
  }

  // the page fills the window under the bars: where it starts (the program's header and the reader's bar) measured
  function fit() {
    const box = stage.hidden ? end : stage;
    const top = Math.max(0, Math.round(box.getBoundingClientRect().top + window.scrollY));
    reader.style.setProperty("--ol-top", top + "px");
  }
  window.addEventListener("resize", fit);

  img.addEventListener("load", () => img.classList.remove("loading"));
  img.addEventListener("error", () => {
    img.classList.remove("loading");
    problem.textContent = "Страница не пришла с сайта — щёлкните по ней, чтобы попробовать ещё раз";
    problem.hidden = false;
  });

  const shown = () => !end.hidden ? total : n;
  function forward() {
    if (!end.hidden) {
      if (reader.dataset.next) location.href = reader.dataset.next;
      return;
    }
    show(n + 1);
  }
  function back() {
    if (!end.hidden) { show(total - 1); return; }
    if (n > 0) show(n - 1);
  }

  // ── clicks: the sides turn, the middle — the bars; a double click zooms ──
  let clickTimer = 0;
  stage.addEventListener("click", (event) => {
    if (zoom.on || zoom.moved) { zoom.moved = false; return; }
    if (!problem.hidden && event.target === img) {          // a page that did not come: asked again
      img.src = pages[n].src + (pages[n].src.includes("?") ? "&" : "?") + "again=" + Date.now();
      problem.hidden = true;
      return;
    }
    const box = stage.getBoundingClientRect();
    const x = (event.clientX - box.left) / Math.max(1, box.width);
    clearTimeout(clickTimer);
    clickTimer = setTimeout(() => {                         // not the first click of a double click
      if (x < 0.3) (rtl ? forward : back)();
      else if (x > 0.7) (rtl ? back : forward)();
      else reader.classList.toggle("ol-bars-off");
    }, 220);
  });
  stage.addEventListener("dblclick", (event) => {
    clearTimeout(clickTimer);
    if (zoom.on) { unzoom(); return; }
    const box = img.getBoundingClientRect();
    zoom = { on: true, scale: 2.5, x: event.clientX - box.left, y: event.clientY - box.top, dx: 0, dy: 0 };
    img.style.transformOrigin = `${zoom.x}px ${zoom.y}px`;
    img.style.transform = `scale(${zoom.scale})`;
    stage.classList.add("zoomed");
  });

  // ── a zoomed page is dragged; a swipe turns a page that is not zoomed ──
  let down = null;
  stage.addEventListener("pointerdown", (event) => {
    down = { x: event.clientX, y: event.clientY, dx: zoom.dx || 0, dy: zoom.dy || 0, t: Date.now() };
    if (zoom.on) stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener("pointermove", (event) => {
    if (!down || !zoom.on) return;
    zoom.dx = down.dx + event.clientX - down.x;
    zoom.dy = down.dy + event.clientY - down.y;
    if (Math.abs(event.clientX - down.x) + Math.abs(event.clientY - down.y) > 6) zoom.moved = true;
    img.style.transform = `translate(${zoom.dx}px, ${zoom.dy}px) scale(${zoom.scale})`;
  });
  stage.addEventListener("pointerup", (event) => {
    const was = down;
    down = null;
    if (!was || zoom.on || event.pointerType === "mouse") return;
    const dx = event.clientX - was.x;
    const dy = event.clientY - was.y;
    if (Math.abs(dx) > 50 && Math.abs(dy) < 60 && Date.now() - was.t < 700) {
      clearTimeout(clickTimer);
      zoom.moved = true;                                    // the click that follows a swipe does nothing
      if (dx > 0) (rtl ? forward : back)();
      else (rtl ? back : forward)();
    }
  });

  // ── the keys ──
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("input, select, textarea") && event.target !== slider) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const key = event.key;
    if ((key === " " || key === "Enter") && event.target.closest("button, a, summary")) return;   // pressed
    if (key === "Escape" && list && !list.hidden) { openList(false); return; }
    if (event.target === slider && (key === "ArrowLeft" || key === "ArrowRight")) return;   // the slider's own
    let done = true;
    if (key === "ArrowLeft") (rtl ? forward : back)();
    else if (key === "ArrowRight") (rtl ? back : forward)();
    else if (key === " " || key === "PageDown") forward();
    else if (key === "PageUp") back();
    else if (key === "Home") show(0);
    else if (key === "End") show(total - 1);
    else done = false;
    if (done) event.preventDefault();
  });

  slider.addEventListener("input", () => { at.textContent = slider.value; });
  slider.addEventListener("change", () => show(Number(slider.value) - 1));

  // ── the list of chapters ──
  function openList(open) {
    if (!list) return;
    list.hidden = !open;
    if (where) where.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      reader.classList.remove("ol-bars-off");
      const here = list.querySelector("li.current");
      if (here) list.scrollTop = here.offsetTop - (list.clientHeight - here.offsetHeight) / 2;
    }
  }
  actions["ol-list"] = () => openList(list && list.hidden);
  document.addEventListener("click", (event) => {
    if (list && !list.hidden && bar && !bar.contains(event.target)) openList(false);
  });

  window.addEventListener("pagehide", () => {
    clearTimeout(saveTimer);
    try {
      fetch(reader.dataset.progress, { method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chapter: reader.dataset.chapter, page: Math.min(shown(), total - 1), part: 0,
          done: shown() >= total - 1 }) });
    } catch (_) { /* the page is going away */ }
  });

  const asked = Number(new URL(location.href).searchParams.get("p") || 0);
  fit();
  show(asked >= 1 ? asked - 1 : n);
})();
