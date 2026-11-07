// Local Booru — (v5.44.123) the menu of covers (webui/pages/covers.py; the owner's approved mockup F — «норм мне
// нравится»): a click on the cover of a title's page (title.js) or of a series' page (series.js) — the covers of the
// sites the title was found on, each with its site and size, the one now marked ✓, «Своя картинка…» (a file of the
// computer, a photo of the phone), «Вернуть обложку сайта».  A click — the cover at once, no «Сохранить»: into the
// title's folder (cover.*, a series' — poster.*; the one before stays beside), or kept by the program for a title
// without a folder.  The computer — a menu beside the cover; the phone (and a narrow window) — from the bottom.
(() => {
  "use strict";
  const title = document.getElementById("title-page");
  const series = document.getElementById("series-page");
  const page = title || series;
  if (!page || (title && title.dataset.gallery)) return;          // a gallery of nhentai: one picture of its own
  const holder = page.querySelector(title ? "[data-tt-cover]" : "[data-sv-cover]");
  if (!holder) return;
  const phone = title ? !!title.dataset.get : series.dataset.phone === "1";
  const VARIANTS = ["вариант", "варианта", "вариантов"];
  const FOLDER = "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 " +
    "3 17.5z";
  const EDIT = "M4 20h4L18.5 9.5l-4-4L4 16zM13 7l4 4";
  const CHECK = "M5 12l5 5L20 7";
  let menu = null;            // the menu open: {root, dim, data}
  let busy = false;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function icon(d, size) {
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("width", String(size));
    s.setAttribute("height", String(size));
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("class", "cv-i");
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d);
    s.append(p);
    return s;
  }
  function plural(n, forms) {
    const a = Math.abs(n) % 100, b = a % 10;
    return n + " " + (a > 10 && a < 20 ? forms[2] : b > 1 && b < 5 ? forms[1] : b === 1 ? forms[0] : forms[2]);
  }
  async function answer(response) {
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || (response.status === 413 ? "Картинка больше 10 МБ — возьмите поменьше"
        : "Программа ответила ошибкой " + response.status));
    }
    return data;
  }
  async function getJson(address) {
    let response;
    try { response = await fetch(address, { credentials: "same-origin", cache: "no-store" }); }
    catch (_) { throw new Error(phone ? "Нет связи с компьютером" : "Нет связи с программой"); }
    return answer(response);
  }
  async function post(address, body, raw) {
    let response;
    try {
      response = await fetch(address, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": raw ? "application/octet-stream" : "application/json" },
        body: raw ? body : JSON.stringify(body || {}) });
    } catch (_) { throw new Error(phone ? "Нет связи с компьютером" : "Нет связи с программой"); }
    return answer(response);
  }

  // which title: the title page's search (title.js puts its number when the title is shown), the series' folder or site
  function where() {
    if (title) return title.dataset.find ? { find: title.dataset.find } : null;
    if (series.dataset.series) return { series: series.dataset.series };
    return series.dataset.url ? { url: series.dataset.url } : null;
  }
  const query = (w) => Object.entries(w).map(([k, v]) => k + "=" + encodeURIComponent(v)).join("&");

  // ── the cover itself: «Сменить обложку» over it (on the computer — at a pointer, on the phone — always) ──────────
  const change = el("button", "cv-change");
  change.type = "button";
  change.append(icon(EDIT, 14), el("span", "", "Сменить обложку"));
  change.setAttribute("aria-haspopup", "dialog");
  function keepChange() {                    // title.js puts the site's cover in place of what was there: back on top
    if (!change.isConnected) holder.append(change);
  }
  holder.classList.add("cv-on");
  if (phone) holder.classList.add("cv-phone");
  keepChange();
  new MutationObserver(keepChange).observe(holder, { childList: true });
  holder.addEventListener("click", (event) => {
    if (event.target.closest("a")) event.preventDefault();
    open();
  });

  function setCover(src) {
    if (!src) return;
    const img = new Image();
    img.alt = "";
    img.decoding = "async";
    if (series) img.dataset.svPoster = "";
    img.onload = () => {
      img.classList.add("lb-in", "lb-now");        // motion.js: a picture of the page is shown once it has come
      holder.querySelectorAll("img").forEach((old) => { if (old !== img) old.remove(); });
      holder.insertBefore(img, holder.firstChild);
    };
    img.src = src;
  }

  // ── the menu ───────────────────────────────────────────────────────────────────────────────────────────────────
  function close() {
    if (!menu) return;
    menu.root.remove();
    if (menu.dim) menu.dim.remove();
    menu = null;
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("mousedown", onOutside, true);
    window.removeEventListener("resize", place);
    change.setAttribute("aria-expanded", "false");
    change.focus({ preventScroll: true });
  }
  function onKey(event) {
    if (event.key === "Escape") { event.preventDefault(); close(); }
  }
  function onOutside(event) {
    if (menu && !menu.root.contains(event.target) && !holder.contains(event.target)) close();
  }

  // beside the cover (the computer, room enough to its right), else from the bottom of the screen
  function sheet() {
    if (phone) return true;
    const r = holder.getBoundingClientRect();
    return window.innerWidth - r.right < 560;
  }
  function place() {
    if (!menu || menu.sheet) return;
    const r = holder.getBoundingClientRect();
    const width = Math.min(880, window.innerWidth - r.right - 40);
    menu.root.style.width = width + "px";
    menu.root.style.left = (r.right + window.scrollX + 16) + "px";
    menu.root.style.top = (r.top + window.scrollY + Math.min(84, r.height / 4)) + "px";
  }

  async function open() {
    const w = where();
    if (!w) return;                                // the title is still read from its site
    if (menu) { close(); return; }
    const side = !sheet();
    const root = el(side ? "div" : "section", "cv-menu" + (side ? "" : " cv-sheet"));
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-label", "Обложка");
    root.dataset.cvMenu = "";
    let dim = null;
    if (!side) {
      dim = el("div", "cv-dim");
      dim.addEventListener("click", close);
      document.body.append(dim);
      root.append(el("span", "cv-handle"));
    }
    const head = el("div", "cv-head");
    const h2 = el("h2", "", "Обложка");
    const sub = el("span", "cv-sub", "Ищу обложки…");
    const x = el("button", "cv-x", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Закрыть");
    x.addEventListener("click", close);
    head.append(h2, sub, x);
    const grid = el("div", "cv-grid");
    const foot = el("p", "cv-foot");
    const status = el("p", "cv-status");
    status.setAttribute("role", "status");
    root.append(head, grid, foot, status);
    document.body.append(root);
    menu = { root, dim, grid, h2, sub, foot, status, where: w, sheet: !side, data: null };
    change.setAttribute("aria-expanded", "true");
    place();
    document.addEventListener("keydown", onKey, true);
    if (side) document.addEventListener("mousedown", onOutside, true);
    window.addEventListener("resize", place);
    await load(true);
  }

  async function load(first) {
    const mine = menu;
    if (!mine) return;
    let data;
    try {
      data = await getJson("/api/covers?" + query(mine.where));
    } catch (err) {
      if (menu !== mine) return;
      mine.sub.textContent = "";
      say(err.message, true);
      return;
    }
    if (menu !== mine) return;
    mine.data = data;
    draw(data);
    if (first) {
      const on = mine.grid.querySelector(".cv-opt.on") || mine.grid.querySelector(".cv-opt");
      if (on) on.focus({ preventScroll: true });
    }
  }

  function say(text, bad) {
    if (!menu) return;
    menu.status.textContent = text || "";
    menu.status.classList.toggle("bad", !!bad);
  }

  function draw(data) {
    const { grid, h2, sub, foot } = menu;
    h2.textContent = "Обложка «" + data.title + "»";
    const n = data.options.length;
    const thing = data.series ? "этот сериал" : "этот тайтл";
    sub.textContent = plural(n, VARIANTS) + (n === 1 ? " — других сайтов, где есть " + thing + ", программа не знает"
      : phone ? " — сайты, где есть " + thing : " — сайты, где программа нашла " + thing);
    grid.textContent = "";
    const shown = [];
    data.options.forEach((o) => {
      const opt = el("button", "cv-opt" + (o.now ? " on" : ""));
      opt.type = "button";
      opt.dataset.k = o.k;
      opt.dataset.kind = o.kind;
      opt.setAttribute("aria-pressed", o.now ? "true" : "false");
      const pic = el("span", "cv-pic");
      const img = el("img");
      img.alt = "";
      img.decoding = "async";
      pic.append(img);
      if (o.now) {
        const tick = el("span", "cv-tick");
        tick.append(icon(CHECK, 14));
        pic.append(tick);
      }
      const size = el("span", "cv-size", "…");
      const note = el("span", "cv-note", o.note || "");
      opt.append(pic, el("span", "cv-site", o.label), size, note);
      opt.setAttribute("aria-label", o.label + (o.now ? " — сейчас" : ""));
      const item = { o, opt, img, size, note, w: 0, h: 0 };
      shown.push(item);
      img.addEventListener("load", () => {
        item.w = img.naturalWidth;
        item.h = img.naturalHeight;
        size.textContent = item.w + " × " + item.h;
        notes(shown);
      }, { once: true });
      img.addEventListener("error", () => {
        opt.classList.add("bad");
        opt.disabled = true;
        size.textContent = "не пришла";
      }, { once: true });
      img.src = data.pic + encodeURIComponent(o.k);
      opt.addEventListener("click", () => pick(item));
      grid.append(opt);
    });
    // «Своя картинка…» — a file of the computer, a photo of the phone
    const own = el("label", "cv-opt cv-own");
    own.tabIndex = 0;
    const input = el("input");
    input.type = "file";
    input.accept = "image/*";
    input.className = "cv-file";
    input.addEventListener("change", () => { if (input.files && input.files[0]) upload(input.files[0]); });
    const pic = el("span", "cv-pic");
    const words = el("span", "cv-own-words");
    if (phone) words.append("Своя", document.createElement("br"), "картинка…");
    else words.append("Своя картинка…", document.createElement("br"), "из файла");
    pic.append(icon(FOLDER, phone ? 24 : 26), words);
    own.append(input, pic, el("span", "cv-site", "Своя"), el("span", "cv-size", phone ? "с телефона" : "JPG, PNG, WebP"));
    own.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
    });
    grid.append(own);
    // what a click does, where the cover goes; «Вернуть обложку сайта»
    foot.textContent = "";
    const file = data.file || (data.series ? "poster.jpg" : "cover.jpg");
    const old = file.replace(/\.[^.]+$/, ".old-….jpg");
    const bold = (text) => el("b", "", text);
    const thing2 = data.series ? "сериал" : "тайтл";
    if (phone) {
      foot.append("Нажатие — сразу новая обложка. ", bold("Выбор помнится"));
      if (data.folder) {
        foot.append("; " + (data.series ? "в папку сериала" : "тайтл с папкой — в папку") + " как ", bold(file),
          ", прежняя — рядом.");
      } else {
        foot.append("; когда " + thing2 + " скачается, в папку ляжет она.");
      }
    } else {
      const one = el("span");
      one.append(bold("Щелчок — сразу новая обложка"), ", без «Сохранить». ");
      one.append(data.series ? "Выбор помнится для сериала: на этой странице, в «Видео» и в «Загрузках»."
        : "Выбор помнится для тайтла: на этой странице, в «" + (title.dataset.kind === "manga" ? "Манге" : "Манхве")
          + "», в «Ленте» и в «Загрузках».");
      const two = el("span");
      if (data.folder) {
        two.append((data.series ? "Выбранная кладётся в папку сериала как " : "Тайтл с папкой — выбранная кладётся в "
          + "папку как "), bold(file), "; прежняя остаётся рядом (" + old + "), не стирается.");
      } else {
        two.append((data.series ? "Сериала" : "Тайтла") + " ещё нет в папке — выбор помнит программа; когда "
          + thing2 + " скачается, в папку ляжет выбранная.");
      }
      foot.append(one, two);
    }
    if (data.back) {
      const back = el("button", "cv-back", "Вернуть обложку сайта");
      back.type = "button";
      back.title = "Обложка " + data.back + ", как у сайта";
      back.addEventListener("click", () => choose({ back: true }, back));
      foot.append(back);
    }
  }

  // «самая крупная», «маленькая» — once the sizes are known (the site's own word «сейчас …» stays)
  function notes(shown) {
    const sites = shown.filter((x) => x.o.kind === "site" && x.w);
    const biggest = sites.length > 1 ? sites.reduce((a, b) => (b.w * b.h > a.w * a.h ? b : a)) : null;
    shown.forEach((x) => {
      if (x.o.note || !x.w) return;
      x.note.textContent = x === biggest ? "самая крупная" : x.w < 400 ? "маленькая" : "";
    });
  }

  function pick(item) {
    if (item.o.now) { say("Эта обложка и стоит"); return; }
    choose({ k: item.o.k }, item.opt);
  }

  async function choose(body, from) {
    if (busy || !menu) return;
    busy = true;
    const mine = menu;
    from.classList.add("busy");
    say("Ставлю обложку…");
    try {
      const reply = await post("/api/covers/choose", { ...mine.where, ...body });
      done(reply, mine);
    } catch (err) {
      if (menu === mine) say(err.message, true);
    } finally {
      from.classList.remove("busy");
      busy = false;
    }
  }

  async function upload(file) {
    if (busy || !menu) return;
    busy = true;
    const mine = menu;
    say("Отправляю картинку…");
    try {
      const reply = await post("/api/covers/own?" + query(mine.where), file, true);
      done(reply, mine);
    } catch (err) {
      if (menu === mine) say(err.message, true);
    } finally {
      busy = false;
    }
  }

  function done(reply, mine) {
    setCover(reply.cover);
    if (menu !== mine) return;
    say(reply.said || "Обложка поставлена");
    load(false);
  }
})();
