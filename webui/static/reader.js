// Local Booru (v5.44.117) — the one reader of the program (webui/pages/reader.py; the owner's approved mockup B «Одна
// читалка»): the downloaded manga, the downloaded manhwa and the chapters of a site read without downloading them, on
// the computer and on the phone.  Until then six readers had their own scripts (booru.js for the manga, manhwa.js for
// the strip, online.js for the pages one at a time, phone.js for the phone's manga).
//
// «По страницам»: one page at a time — its left or right half (the phone: its sides; the middle shows and hides the
// panels), ← → (right to left — ← goes on), Space / PageDown — on, PageUp — back, Shift+← → — the chapter, Home / End —
// the first and the last page; a swipe and the slider on the phone; a double click — ×2.5 where it was made, then the
// page is dragged (the phone: a double tap in the middle — its sides turn a page at each tap, however quick); the next
// pages are asked ahead; «Лента страниц» under the page, beside it or none; after the last
// page — one screen «Глава закончилась» (→ or Space — the next chapter, ← — the last page again).  The address keeps
// the page (?p=), the place goes to the program a moment after a turn; «назад» from the first page — the chapter
// before, at its last page.
// «Прокруткой»: the strip of «Манхва» — the place found again (a page and a part of it), the chapter at the top
// followed by the panel and the address, a chapter read to its end ticked, the next chapter brought below by itself
// with the words between the chapters, the panel hiding while the strip is read down; ↑ ↓ Space scroll, Shift+← → —
// the chapter, Home / End — the start and the end of the chapter.
// The way is kept for the title (data.modeApi); the left-to-right / right-to-left switch needs no new page.
(() => {
  "use strict";
  const root = document.querySelector("[data-rd]");
  if (!root) return;
  let data;
  try {
    data = JSON.parse(root.querySelector(".rd-data").textContent);
  } catch (_) {
    return;                                        // the reader without its script: its links still turn its pages
  }
  const LB = window.LocalBooru || {};
  const say = LB.say || (() => {});
  const phone = !!data.phone;
  const pages = data.pages || [];
  const total = pages.length;
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";     // the place is the reader's own

  // ── talking to the program ─────────────────────────────────────────────────────────────────────────────────
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) })
      .then((r) => r.json().catch(() => ({})).then((d) => {
        if (!r.ok || d.ok === false) throw new Error(d.error || "программа ответила ошибкой " + r.status);
        return d;
      }));
  }
  function postLast(url, body) {                   // the page is going away: the request outlives it
    try {
      fetch(url, { method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (_) { /* nothing to do: the page is gone */ }
  }
  const withP = (href, p) => href + (href.includes("?") ? "&" : "?") + "p=" + p;
  const chapterHref = (key) => data.base.replace("{key}", encodeURIComponent(key));

  // ‹ › of the chapters: a link to a chapter, or none
  function arrow(rel, href) {
    const old = root.querySelector('[data-rd-arrow="' + rel + '"]');
    if (!old) return;
    const words = rel === "prev" ? "Предыдущая глава" : "Следующая глава";
    const el = document.createElement(href ? "a" : "span");
    el.className = old.className;
    el.textContent = old.textContent;
    el.dataset.rdArrow = rel;
    if (href) {
      el.href = href;
      el.rel = rel;
      el.title = words + (phone ? "" : rel === "prev" ? " (Shift+←)" : " (Shift+→)");
      el.setAttribute("aria-label", words);
    } else {
      el.setAttribute("aria-disabled", "true");
      el.setAttribute("aria-label", rel === "prev" ? "Предыдущей главы нет" : "Следующей главы нет");
    }
    old.replaceWith(el);
  }

  // ── the panel ──────────────────────────────────────────────────────────────────────────────────────────────
  const bars = Array.from(root.querySelectorAll("[data-rd-bar]"));
  const list = root.querySelector(".rd-list");
  const listButton = root.querySelector('[data-rd="list"]');
  const select = root.querySelector('select[data-rd="page"]');
  const slider = root.querySelector('[data-rd="slider"]');
  const atText = root.querySelector("[data-rd-at]");
  const label = root.querySelector("[data-rd-label]");
  const endWords = Array.from(root.querySelectorAll("[data-rd-endword]"));

  function barsShown(on) {
    root.classList.toggle("rd-bare", !on);
    bars.forEach((b) => b.classList.toggle("rd-away", !on && !phone));
  }

  function openList(open) {
    if (!list) return;
    list.hidden = !open;
    if (listButton) listButton.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      barsShown(true);
      const here = list.querySelector("li.current");
      if (here) list.scrollTop = here.offsetTop - (list.clientHeight - here.offsetHeight) / 2;
      if (siteState === "asking") list.querySelector("[data-rd-site-wait]").hidden = false;
    }
  }

  // the page shown in the panel: «Страница N из M» (the computer — its list, the phone — its slider)
  function counter(n, count, atEnd) {
    const shown = Math.min(n + 1, Math.max(1, count));
    if (select) {
      if (select.options.length !== Math.max(1, count)) {
        select.textContent = "";
        for (let k = 1; k <= Math.max(1, count); k++) select.append(new Option(String(k), String(k)));
      }
      select.value = String(shown);
    }
    if (slider) {
      slider.max = String(Math.max(1, count));
      slider.value = String(shown);
    }
    if (atText) atText.textContent = String(shown);
    root.querySelectorAll("[data-rd-total]").forEach((el) => { el.textContent = String(count); });
    endWords.forEach((el) => { el.hidden = !atEnd; });
    const here = list && list.querySelector("li.current .rd-st.here");
    if (here) here.textContent = "сейчас · стр. " + shown + " из " + count;
  }

  // the way it is read: kept for the title; the strip and the pages are two pages of the program (the place kept)
  let leaving = null;                              // set by the way of reading: where the reader goes now
  async function chooseWay(button, way) {
    try {
      await post(data.modeApi, { mode: way });
    } catch (err) {
      say(button, err.message, true);
      return;
    }
    if (leaving) leaving(way);
  }

  // ── the clicks of the panel and of the list ──
  root.addEventListener("click", (event) => {
    const el = event.target.closest("[data-rd]");
    if (!el || !root.contains(el)) return;
    const what = el.dataset.rd;
    if (what === "list") {
      event.preventDefault();
      openList(list.hidden);
    } else if (what === "mode") {
      event.preventDefault();
      if (el.getAttribute("aria-pressed") === "true" && el.dataset.mode === data.mode) return;
      chooseWay(el, el.dataset.mode);
    }
  });
  document.addEventListener("click", (event) => {
    if (list && !list.hidden && !event.target.closest(".rd-list, [data-rd='list']")) openList(false);
  });
  // a click of the mouse (or a tap) leaves no focus on the reader's buttons and links: Space and Enter go on to the
  // reader («Назад к страницам», then → and Space — the next chapter, not that button again)
  root.addEventListener("click", (event) => {
    const el = event.target.closest && event.target.closest("button, a");
    if (el && event.detail > 0) el.blur();
  }, true);

  // ── a folder's title of a site: its chapters there (the list, the end of the chapter) ──
  let siteState = data.siteApi ? "asking" : "none";
  const siteHooks = [];                            // the way of reading takes the answer too
  function askSite(tries) {
    if (!data.siteApi) return;
    fetch(data.siteApi, { credentials: "same-origin" })
      .then((r) => r.json())
      .then((reply) => {
        if (reply.pending && tries < 24) {
          setTimeout(() => askSite(tries + 1), 1500);
          return;
        }
        siteState = "done";
        const wait = list && list.querySelector("[data-rd-site-wait]");
        if (wait) wait.hidden = true;
        if (!reply.ok || reply.pending || !reply.list) return;
        const rows = list && list.querySelector("[data-rd-rows]");
        if (rows) {
          const current = rows.querySelector("li.current");
          const key = current ? current.dataset.chapter : data.chapter;
          const said = current && current.querySelector(".rd-st.here");
          rows.innerHTML = reply.list;
          rows.querySelectorAll("li").forEach((li) => li.classList.toggle("current", li.dataset.chapter === key));
          const now = rows.querySelector("li.current .rd-st.here");
          if (now && said) now.textContent = said.textContent;
        }
        if (reply.next) {
          data.next = reply.next;
          const old = root.querySelector('[data-rd-arrow="next"]');
          if (old && old.getAttribute("aria-disabled") === "true") arrow("next", reply.next.href);
        }
        siteHooks.forEach((hook) => hook(reply));
      })
      .catch(() => { siteState = "done"; });
  }

  // ── «По страницам» ─────────────────────────────────────────────────────────────────────────────────────────
  function pagesMode() {
    const stage = root.querySelector("[data-rd-stage]");
    const img = stage.querySelector(".rd-page");
    const end = root.querySelector("[data-rd-end]");
    const problem = stage.querySelector("[data-rd-problem]");
    const book = root.querySelector("[data-rd-book]");
    const thumbs = root.querySelector("[data-rd-thumbs]");
    const zoneL = stage.querySelector(".rd-zone-l");
    const zoneR = stage.querySelector(".rd-zone-r");
    const AHEAD = 4;                               // pages asked for ahead
    const kept = new Map();
    let rtl = data.mode === "rtl";
    let n = Math.max(0, Math.min(Number(data.page) || 0, total));        // total — the end screen
    let saveTimer = 0;
    let turned = false;
    let zoom = { on: false, scale: 2.5, x: 0, y: 0, dx: 0, dy: 0, moved: false };

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
    function place() {
      return { chapter: data.chapter, page: Math.min(n, total - 1), part: 0, done: n >= total - 1 };
    }
    function save() {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => { post(data.progress, place()).catch(() => {}); }, 700);
    }
    function unzoom() {
      zoom = { on: false, scale: 2.5, x: 0, y: 0, dx: 0, dy: 0, moved: false };
      img.style.transform = "";
      stage.classList.remove("rd-zoomed");
    }
    function links() {                             // the halves' own links (the page works without its script too)
      const back = n >= total ? withP(data.href, total) : n > 0 ? withP(data.href, n)
        : (data.prev ? data.prev.last : data.home);
      const on = n >= total ? (data.next ? data.next.href : data.home) : withP(data.href, n + 1 < total ? n + 2 : "end");
      zoneL.href = rtl ? on : back;
      zoneR.href = rtl ? back : on;
      zoneL.setAttribute("aria-label", rtl ? "Дальше" : "Назад");
      zoneR.setAttribute("aria-label", rtl ? "Назад" : "Дальше");
    }
    function marks() {                             // the strip of pages: the page shown, kept in view
      if (!thumbs) return;
      let shown = null;
      thumbs.querySelectorAll(".rd-thumb").forEach((a) => {
        const on = Number(a.dataset.n) === n + 1;
        a.classList.toggle("current", on);
        if (on) { a.setAttribute("aria-current", "page"); shown = a; } else a.removeAttribute("aria-current");
      });
      if (shown && thumbs.offsetParent) {
        thumbs.scrollLeft = shown.offsetLeft - (thumbs.clientWidth - shown.offsetWidth) / 2;
        thumbs.scrollTop = shown.offsetTop - (thumbs.clientHeight - shown.offsetHeight) / 2;
      }
    }
    function paint() {
      const atEnd = n >= total;
      counter(Math.min(n, total - 1), total, atEnd);
      marks();
      links();
      const name = data.single ? data.name : data.label + " — " + data.name;
      document.title = (atEnd ? "Конец" : (n + 1) + "/" + total) + " — " + name + " — Local Booru";
      history.replaceState(history.state, "", withP(data.href, atEnd ? "end" : n + 1));
    }
    function show(k, quiet) {
      k = Math.max(0, Math.min(k, total));
      const was = n;
      n = k;
      const atEnd = k >= total;
      unzoom();
      stage.hidden = atEnd;
      end.hidden = !atEnd;
      if (!atEnd) {
        problem.hidden = true;
        const page = pages[k];
        if (want !== page.src) put(page, k, quiet || k === was ? "" : k > was ? "next" : "prev");
        ahead();
      }
      paint();
      if (!quiet) {
        save();
        if (phone && !turned) { turned = true; barsShown(false); }
      }
    }
    // A page is put on the screen when it has come whole and is decoded (the page before stays, dimmed, meanwhile):
    // a big picture drawn while it arrives goes in stripes, and on some graphics drivers such a half-drawn frame
    // stayed on the screen (booru.js setupPicture keeps a card's picture back for the same reason).
    let ticket = 0;
    let want = img.getAttribute("src");           // the page asked for last (on the screen, or still coming)
    function put(page, k, way) {
      const mine = ++ticket;
      want = page.src;
      if (img.getAttribute("src") === page.src) {  // back to the page on the screen: the one still coming is not wanted
        img.classList.remove("loading");
        return;
      }
      img.classList.add("loading");
      const pic = kept.get(k) || new Image();
      pic.decoding = "async";
      if (pic.getAttribute("src") !== page.src) pic.src = page.src;
      const swap = () => {
        if (mine !== ticket) return;
        img.src = page.src;
        img.alt = "Страница " + (k + 1) + " из " + total;
        if (page.w > 0 && page.h > 0) { img.width = page.w; img.height = page.h; }
        else { img.removeAttribute("width"); img.removeAttribute("height"); }
        if (img.complete && img.naturalWidth) img.classList.remove("loading");
        if (way && document.documentElement.dataset.motion === "on") {
          stage.dataset.turn = "";                 // the page comes from the side it was turned to
          void stage.offsetWidth;
          stage.dataset.turn = way;
        }
      };
      pic.decode().then(swap, swap);               // a page that does not open: its own «error» says so
    }
    const forward = () => { if (n >= total) nextChapter(); else show(n + 1); };
    const back = () => { if (n >= total) show(total - 1); else if (n > 0) show(n - 1); else prevChapter(true); };
    function nextChapter() { if (data.next) location.href = data.next.href; }
    function prevChapter(last) { if (data.prev) location.href = last ? data.prev.last : data.prev.href; }

    img.addEventListener("load", () => img.classList.remove("loading"));
    img.addEventListener("error", () => {
      img.classList.remove("loading");
      problem.textContent = data.src === "site" ? "Страница не пришла с сайта — щёлкните по ней, чтобы попробовать ещё раз"
        : "Страница не открылась — щёлкните по ней, чтобы попробовать ещё раз";
      problem.hidden = false;
    });

    // ── the halves (the phone: its sides, the middle — the panels); a double click zooms ──
    let clickTimer = 0;
    stage.addEventListener("click", (event) => {
      event.preventDefault();
      if (zoom.on || zoom.moved) { zoom.moved = false; return; }
      if (!problem.hidden) {                       // a page that did not come: asked again
        const page = pages[n];
        img.src = page.src + (page.src.includes("?") ? "&" : "?") + "again=" + Date.now();
        problem.hidden = true;
        return;
      }
      const box = stage.getBoundingClientRect();
      const x = (event.clientX - box.left) / Math.max(1, box.width);
      const middle = phone && x >= 0.3 && x <= 0.7;
      clearTimeout(clickTimer);
      if (phone && !middle) {                      // the phone's sides: the page at once (two quick taps — two pages)
        if (x < 0.3) (rtl ? forward : back)();
        else (rtl ? back : forward)();
        return;
      }
      clickTimer = setTimeout(() => {              // not the first click of a double click
        if (middle) { barsShown(root.classList.contains("rd-bare")); return; }
        if (x < 0.5) (rtl ? forward : back)();
        else (rtl ? back : forward)();
      }, 200);
    });
    const sideOf = (event) => {
      const box = stage.getBoundingClientRect();
      const x = (event.clientX - box.left) / Math.max(1, box.width);
      return x < 0.3 || x > 0.7;
    };
    stage.addEventListener("dblclick", (event) => {
      if (phone && sideOf(event)) return;          // the phone: a double tap zooms in the middle (its sides turn pages)
      clearTimeout(clickTimer);
      if (zoom.on) { unzoom(); return; }
      const box = img.getBoundingClientRect();
      zoom = { on: true, scale: 2.5, x: event.clientX - box.left, y: event.clientY - box.top, dx: 0, dy: 0, moved: false };
      img.style.transformOrigin = zoom.x + "px " + zoom.y + "px";
      img.style.transform = "scale(" + zoom.scale + ")";
      stage.classList.add("rd-zoomed");
    });
    // a zoomed page is dragged; a swipe turns a page that is not zoomed
    let down = null;
    stage.addEventListener("pointerdown", (event) => {
      down = { x: event.clientX, y: event.clientY, dx: zoom.dx, dy: zoom.dy, t: Date.now() };
      zoom.moved = false;                          // the click of this press is its own (a swipe before may have none)
      if (zoom.on) stage.setPointerCapture(event.pointerId);
    });
    stage.addEventListener("pointermove", (event) => {
      if (!down) return;
      if (zoom.on) {
        zoom.dx = down.dx + event.clientX - down.x;
        zoom.dy = down.dy + event.clientY - down.y;
        if (Math.abs(event.clientX - down.x) + Math.abs(event.clientY - down.y) > 6) zoom.moved = true;
        img.style.transform = "translate(" + zoom.dx + "px, " + zoom.dy + "px) scale(" + zoom.scale + ")";
      } else if (event.pointerType !== "mouse") {
        const dx = event.clientX - down.x;
        if (Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(event.clientY - down.y)) img.style.translate = dx / 2 + "px 0";
      }
    });
    stage.addEventListener("pointerup", (event) => {
      const was = down;
      down = null;
      img.style.translate = "";
      if (!was || zoom.on || event.pointerType === "mouse") return;
      const dx = event.clientX - was.x;
      const dy = event.clientY - was.y;
      if (Math.abs(dx) > 50 && Math.abs(dy) < 80 && Date.now() - was.t < 900) {
        clearTimeout(clickTimer);
        zoom.moved = true;                         // the click that follows a swipe does nothing
        if (dx < 0) (rtl ? back : forward)();
        else (rtl ? forward : back)();
      }
    });
    stage.addEventListener("pointercancel", () => { down = null; img.style.translate = ""; });
    // the end screen: a swipe on it too (the phone), its buttons
    end.addEventListener("click", (event) => {
      const button = event.target.closest('[data-rd="back-to-pages"]');
      if (button) { event.preventDefault(); show(total - 1); }
    });
    let endDown = null;
    end.addEventListener("pointerdown", (event) => { endDown = { x: event.clientX, y: event.clientY }; });
    end.addEventListener("pointerup", (event) => {
      const was = endDown;
      endDown = null;
      if (!was || event.pointerType === "mouse") return;
      const dx = event.clientX - was.x;
      if (Math.abs(dx) > 50 && Math.abs(event.clientY - was.y) < 80) {
        if ((dx < 0) !== rtl) nextChapter(); else show(total - 1);
      }
    });

    // ── the strip of pages and its place ──
    if (thumbs) {
      thumbs.addEventListener("click", (event) => {
        const a = event.target.closest(".rd-thumb");
        if (!a) return;
        event.preventDefault();
        show(Number(a.dataset.n) - 1);
      });
    }
    root.addEventListener("click", (event) => {
      const el = event.target.closest('[data-rd="place"], [data-rd="page-prev"], [data-rd="page-next"]');
      if (!el) return;
      event.preventDefault();
      if (el.dataset.rd === "page-prev") { if (n > 0) show(Math.min(n, total) - 1); return; }
      if (el.dataset.rd === "page-next") { if (n < total) show(n + 1); return; }
      const where = el.dataset.place;
      book.classList.remove("strip-bottom", "strip-side", "strip-off");
      book.classList.add("strip-" + where);
      root.querySelectorAll('[data-rd="place"]').forEach((b) => {
        const on = b === el;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      fit();
      marks();
      post("/api/prefs", { manga_strip: where }).catch(() => {});
    });
    if (select) select.addEventListener("change", () => show(Number(select.value) - 1));
    if (slider) {
      slider.addEventListener("input", () => { if (atText) atText.textContent = slider.value; });
      slider.addEventListener("change", () => show(Number(slider.value) - 1));
    }

    // ── the keys ──
    document.addEventListener("keydown", (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.defaultPrevented) return;
      const target = event.target;
      if (target.closest && target.closest("input, select, textarea") && target !== slider) return;
      const key = event.key;
      if ((key === " " || key === "Enter") && target.closest && target.closest("button, a, summary")) return;
      if (target === slider && (key === "ArrowLeft" || key === "ArrowRight")) return;     // the slider's own
      if (key === "Escape" && list && !list.hidden) { openList(false); if (listButton) listButton.focus(); }
      else if (key === "ArrowLeft" && event.shiftKey) (rtl ? nextChapter : () => prevChapter(false))();
      else if (key === "ArrowRight" && event.shiftKey) (rtl ? () => prevChapter(false) : nextChapter)();
      else if (key === "ArrowLeft") (rtl ? forward : back)();
      else if (key === "ArrowRight") (rtl ? back : forward)();
      else if (key === " " && event.shiftKey) back();
      else if (key === " " || key === "PageDown") forward();
      else if (key === "PageUp") back();
      else if (key === "Home") show(0);
      else if (key === "End") show(total - 1);
      else return;
      event.preventDefault();
    });

    // ── right to left and back: the same page, the other way (no new page) ──
    function turnWay(way) {
      rtl = way === "rtl";
      data.mode = way;
      root.dataset.dir = way;
      root.querySelectorAll('[data-rd="mode"]').forEach((b) => {
        if (b.closest(".rd-seg") && b.dataset.mode !== "strip") b.dataset.mode = way;   // «По страницам» goes on so
        const on = b.dataset.mode === way;
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.classList.toggle("active", on && !!b.closest(".rd-dir"));
      });
      if (slider) slider.dir = rtl ? "rtl" : "ltr";
      root.querySelectorAll("[data-rd-on]").forEach((k) => { k.textContent = rtl ? "←" : "→"; });
      root.querySelectorAll("[data-rd-back-key]").forEach((k) => { k.textContent = rtl ? "→" : "←"; });
      root.querySelectorAll("[data-rd-swipe]").forEach((s) => { s.textContent = rtl ? "вправо" : "влево"; });
      links();
    }
    leaving = (way) => {
      if (way === "strip") {
        clearTimeout(saveTimer);
        post(data.progress, place()).catch(() => {}).finally(() => location.replace(withP(data.href, Math.min(n, total - 1) + 1)));
      } else turnWay(way);
    };
    siteHooks.push((reply) => {                    // the site's chapters: the end screen says where the next one is
      if (reply.end) end.innerHTML = reply.end;
      turnWay(data.mode);
      links();
    });

    // the page fills the window under the panel (and over the strip of pages when there is room for both)
    function fit() {
      if (phone) return;
      const panel = root.querySelector(".rd-panel");
      const places = root.querySelector(".rd-places");
      const room = window.innerHeight - (panel ? panel.offsetHeight : 0) - (places ? places.offsetHeight : 0) - 24;
      const under = book.classList.contains("strip-bottom") && thumbs ? thumbs.offsetHeight + 8 : 0;
      const height = room - under >= 420 ? room - under : room;      // a small window: the page first, the strip below
      root.style.setProperty("--rd-h", Math.max(240, Math.round(height)) + "px");
    }
    window.addEventListener("resize", fit);
    window.addEventListener("pagehide", () => {
      clearTimeout(saveTimer);
      postLast(data.progress, place());
    });

    fit();
    show(n, true);
    if (!phone) {                                  // the panel on top of the window: the page whole under it
      const top = root.getBoundingClientRect().top + window.scrollY;
      if (book.getBoundingClientRect().bottom > window.innerHeight && top > 0) window.scrollTo(0, top);
    }
  }

  // ── «Прокруткой» ───────────────────────────────────────────────────────────────────────────────────────────
  function stripMode() {
    const strip = root.querySelector(".rd-strip");
    const KEEP = 4;                                // chapters kept in the page at once: the oldest goes for a fifth
    const AHEAD = 2.5;                             // the next chapter is asked this many windows before the end
    const done = new Set();                        // chapters ticked in this visit
    let current = null;
    let loading = false;
    let saveTimer = 0;
    let shownPage = -1;
    const sections = () => Array.from(strip.querySelectorAll(".rd-chapter[data-chapter]"));
    const pagesOf = (section) => Array.from(section.querySelectorAll("img.rd-slice"));

    // where the strip is read: the chapter at the top of the window, its page there and how much of it is above
    function spot() {
      const edge = topEdge();
      for (const section of sections()) {
        const box = section.getBoundingClientRect();
        if (box.bottom <= edge) continue;
        const imgs = pagesOf(section);
        let index = 0;
        let lo = 0;
        let hi = imgs.length - 1;
        while (lo <= hi) {                         // the last page whose top is above the window's top (a pixel
          const mid = (lo + hi) >> 1;              // of rounding after a jump to a page's top — that page)
          if (imgs[mid].getBoundingClientRect().top <= edge + 1) { index = mid; lo = mid + 1; } else { hi = mid - 1; }
        }
        const rect = imgs[index] ? imgs[index].getBoundingClientRect() : null;
        const part = rect && rect.height > 0 ? Math.min(1, Math.max(0, (edge - rect.top) / rect.height)) : 0;
        return { section, page: index, part };
      }
      return null;
    }
    function topEdge() { return 0; }
    // the chapter after this one that the strip does not bring itself (the site's, another source's): the link of the
    // words after it ("" — none)
    function beyond(section) {
      const link = section.querySelector(".rd-between a.rd-end-next");
      return link ? link.getAttribute("href") : "";
    }
    function goTo(section, page, part) {
      const imgs = pagesOf(section);
      const img = imgs[Math.min(page, imgs.length - 1)];
      if (!img) return;
      const rect = img.getBoundingClientRect();
      window.scrollTo(0, window.scrollY + rect.top + part * rect.height - topEdge());
    }

    // the panel, the address and the title follow the chapter being read
    function follow(section) {
      if (section === current) return;
      current = section;
      const key = section.dataset.chapter;
      data.chapter = key;
      data.label = section.dataset.label;
      if (label) label.textContent = phone ? section.dataset.label : section.dataset.full;
      const all = sections();
      arrow("prev", section.dataset.prev ? chapterHref(section.dataset.prev) : (section === all[0] && data.prev ? data.prev.href : ""));
      arrow("next", section.dataset.next ? chapterHref(section.dataset.next) : beyond(section));
      if (list) list.querySelectorAll("li[data-chapter]").forEach((li) => li.classList.toggle("current", li.dataset.chapter === key));
      document.title = section.dataset.label + " — " + data.name + " — Local Booru";
      shownPage = -1;
    }

    function save(now, extra) {
      const at = spot();
      if (!at) return;
      const body = Object.assign({ chapter: at.section.dataset.chapter, page: at.page, part: Number(at.part.toFixed(4)) },
        extra || {});
      history.replaceState(history.state, "", chapterHref(body.chapter));
      if (now) postLast(data.progress, body);
      else post(data.progress, body).catch(() => {});
    }

    function tick(section) {
      const key = section.dataset.chapter;
      if (done.has(key)) return;
      done.add(key);
      post(data.progress, { chapter: key, page: Math.max(0, pagesOf(section).length - 1), part: 1, done: true })
        .catch(() => { done.delete(key); });
      const row = list && list.querySelector('li[data-chapter="' + CSS.escape(key) + '"]');
      if (row) row.classList.add("read");
    }

    // the next chapter, glued below the strip; the oldest one leaves (the place kept)
    async function more() {
      const all = sections();
      const last = all[all.length - 1];
      if (loading || !last || !last.dataset.next || !data.chapterApi) return;
      loading = true;
      try {
        const response = await fetch(data.chapterApi.replace("{key}", encodeURIComponent(last.dataset.next)),
          { credentials: "same-origin" });
        const answer = await response.json();
        if (!response.ok || !answer.ok) throw new Error(answer.error || "глава не открылась");
        if (strip.querySelector('.rd-chapter[data-chapter="' + CSS.escape(String(answer.id)) + '"]')) return;
        const section = document.createElement("section");
        section.className = "rd-chapter";
        Object.assign(section.dataset, { chapter: answer.id, label: answer.label, full: answer.full || answer.label,
          prev: answer.prev || "", next: answer.next || "", pages: String(answer.pages.length) });
        const head = document.createElement("h2");
        head.className = "rd-chapter-head";
        head.textContent = answer.full || answer.label;
        section.append(head);
        answer.pages.forEach((p, k) => {
          const img = document.createElement("img");
          img.className = "reader-image rd-slice";
          img.id = "c" + answer.id + "-p" + (k + 1);
          img.alt = "";
          img.decoding = "async";
          if (k > 1) img.loading = "lazy";
          if (p.w > 0 && p.h > 0) { img.width = p.w; img.height = p.h; }
          img.src = p.src;
          section.append(img);
        });
        if (answer.between) section.insertAdjacentHTML("beforeend", answer.between);
        strip.append(section);
        while (sections().length > KEEP) {         // the oldest leaves; what is read stays in its place
          const first = sections()[0];
          if (first === current) break;
          const before = sections()[1].getBoundingClientRect().top;
          first.remove();
          const after = sections()[0].getBoundingClientRect().top;
          window.scrollBy(0, after - before);
        }
      } catch (err) {
        say(label || root, "Следующая глава не открылась: " + err.message, true);
      } finally {
        loading = false;
      }
    }

    // ── scrolling: the panel, the place, the ticks, the next chapter ──
    let lastY = window.scrollY;
    let pending = false;
    function onScroll() {
      pending = false;
      const y = window.scrollY;
      const dy = y - lastY;
      lastY = y;
      if (!list || list.hidden) {
        const panel = bars[0];
        const low = panel ? panel.offsetHeight + 40 : 80;
        if (dy > 4 && y > low) barsShown(false);
        else if (dy < -4 || y < 40) barsShown(true);
      }
      const at = spot();
      if (at) {
        follow(at.section);
        if (at.page !== shownPage) {
          shownPage = at.page;
          counter(at.page, pagesOf(at.section).length, false);
        }
      }
      for (const section of sections()) {          // read to its end: its last page's bottom in the window
        const imgs = pagesOf(section);
        const last = imgs[imgs.length - 1];
        if (last && last.getBoundingClientRect().bottom <= window.innerHeight + 2 && window.scrollY > 0) tick(section);
      }
      if (strip.getBoundingClientRect().bottom - window.innerHeight < AHEAD * window.innerHeight) more();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => save(false), 1200);
    }
    window.addEventListener("scroll", () => {
      if (!pending) {
        pending = true;
        requestAnimationFrame(onScroll);
      }
    }, { passive: true });
    window.addEventListener("pagehide", () => save(true));
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") save(true); });

    // a tap on the strip (the phone): the panels shown or hidden
    if (phone) {
      strip.addEventListener("click", (event) => {
        if (event.target.closest("a, button")) return;
        barsShown(root.classList.contains("rd-bare"));
      });
    }

    // ── the panel: the page of the chapter, the width ──
    const jump = (k) => { if (current) goTo(current, Math.max(0, k), 0); };
    root.addEventListener("click", (event) => {
      const el = event.target.closest('[data-rd="width"], [data-rd="page-prev"], [data-rd="page-next"]');
      if (!el) return;
      event.preventDefault();
      const at = spot();
      if (el.dataset.rd === "page-prev") { if (at) jump(at.part > 0.02 ? at.page : at.page - 1); return; }
      if (el.dataset.rd === "page-next") { if (at) jump(at.page + 1); return; }
      const width = el.dataset.width;
      strip.style.setProperty("--rd-w", width === "full" ? "100%" : width + "px");
      strip.dataset.width = width;
      root.querySelectorAll('[data-rd="width"]').forEach((b) => {
        const on = b === el;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      if (at) goTo(at.section, at.page, at.part);
      post("/api/prefs", { manhwa_width: width }).catch(() => {});
    });
    if (select) select.addEventListener("change", () => jump(Number(select.value) - 1));
    if (slider) {
      slider.addEventListener("input", () => { if (atText) atText.textContent = slider.value; });
      slider.addEventListener("change", () => jump(Number(slider.value) - 1));
    }

    // ── the keys: ↑ ↓ Space — the browser's own; Shift+← → (and ← →) — the chapter; Home / End — its start, its end ──
    document.addEventListener("keydown", (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.defaultPrevented) return;
      const target = event.target;
      if (target.closest && target.closest("input, select, textarea")) return;
      const key = event.key;
      if (key === "Escape" && list && !list.hidden) { openList(false); if (listButton) listButton.focus(); }
      else if (key === "ArrowLeft" || key === "ArrowRight") {
        const section = current;
        if (!section) return;
        const target2 = key === "ArrowLeft" ? section.dataset.prev : section.dataset.next;
        if (target2) location.href = chapterHref(target2);
        else if (key === "ArrowRight" && beyond(section)) location.href = beyond(section);
        else return;
      } else if (key === "Home" && current) goTo(current, 0, 0);
      else if (key === "End" && current) {
        const imgs = pagesOf(current);
        const last = imgs[imgs.length - 1];
        if (!last) return;
        const rect = last.getBoundingClientRect();
        window.scrollTo(0, window.scrollY + rect.bottom - window.innerHeight);
      } else return;
      event.preventDefault();
    });

    leaving = () => {                               // «По страницам»: the page at the top of the window
      clearTimeout(saveTimer);
      const at = spot();
      const key = at ? at.section.dataset.chapter : data.chapter;
      const page = at ? at.page + 1 : 1;
      post(data.progress, { chapter: key, page: page - 1, part: at ? Number(at.part.toFixed(4)) : 0 })
        .catch(() => {}).finally(() => location.replace(withP(chapterHref(key), page)));
    };
    siteHooks.push((reply) => {                     // the words after the folder's chapters the site's come after
      Object.entries(reply.betweens || {}).forEach(([key, html]) => {
        const section = strip.querySelector('.rd-chapter[data-chapter="' + CSS.escape(key) + '"]');
        const old = section && section.querySelector(".rd-between");
        if (old) old.outerHTML = html;
      });
      const now = current;                          // the panel's › — the site's next chapter too
      current = null;
      if (now) follow(now);
    });

    // ── the start: the place found again ──
    const first = sections()[0];
    if (first) {
      const page = Number(data.page || 0);
      const part = Number(data.part || 0);
      if (page > 0 || part > 0) goTo(first, page, part);
      follow(first);
      counter(page, pagesOf(first).length, false);
    }
    lastY = window.scrollY;                       // the panel is shown on arrival, wherever the strip opens
    onScroll();
  }

  if (root.dataset.mode === "strip") stripMode(); else pagesMode();
  if (data.siteApi) setTimeout(() => askSite(0), 300);
})();
