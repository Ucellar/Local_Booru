// Local Booru pages: looks, the tag drawer, tag hints while typing, arrow keys, buttons on the post page.
(() => {
  "use strict";

  const root = document.documentElement;

  // The look and the thumbnail size are kept by the program (one file for the window and any browser).
  function remember(key, value) {
    postJson("/api/prefs", { [key]: value }).catch((err) => console.warn("prefs not saved:", err.message));
  }

  // ── looks ────────────────────────────────────────────────────────────────
  function skinMenu() {
    return document.querySelector(".skin-menu");
  }

  function openSkinMenu(open) {
    const menu = skinMenu();
    if (!menu) return;
    const list = menu.querySelector(".skin-list");
    const button = menu.querySelector(".skin-button");
    list.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      // the list hangs from the button's right edge; a button near the left edge (a header that wrapped) — from its
      // left edge, or the list would start outside the window
      list.style.left = "";
      list.style.right = "";
      if (list.getBoundingClientRect().left < 8) {
        list.style.left = "0";
        list.style.right = "auto";
      }
      // (v5.44.59) as tall as the window has room for below it: a header that wrapped in a narrow window (one more
      // item in the menu — «Манхва») puts the list lower than the 72 px of the stylesheet allow for
      list.style.maxHeight = "";
      const room = window.innerHeight - list.getBoundingClientRect().top - 8;
      if (room < list.getBoundingClientRect().height) list.style.maxHeight = Math.max(160, room) + "px";
      const current = list.querySelector(".skin-item.active") || list.querySelector(".skin-item");
      if (current) current.focus();
    }
  }

  // The clock of the «Win98» taskbar (hidden in the other looks).
  function tick() {
    const clock = document.querySelector(".tray-clock");
    if (!clock || getComputedStyle(clock).display === "none") return;
    clock.textContent = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  setInterval(tick, 15000);
  document.addEventListener("DOMContentLoaded", tick);

  // Every page of the menu keeps a look of its own (data-skin-key); «gallery» — the page looks like the gallery.
  function showSkin(id) {
    const link = document.getElementById("skin-css");
    if (link && root.dataset.skin !== id) {
      link.addEventListener("load", tick, { once: true });
      link.href = "/static/skins/" + id + ".css";
    }
    root.dataset.skin = id;
  }

  function markSkin(choice) {
    document.querySelectorAll(".skin-item[data-skin]").forEach((item) => {
      const on = item.dataset.skin === choice;
      item.classList.toggle("active", on);
      item.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function applySkin(choice) {
    const key = root.dataset.skinKey || "skin";
    const id = choice === "gallery" ? (root.dataset.gallerySkin || "auto") : choice;
    showSkin(id);
    if (key === "skin") root.dataset.gallerySkin = id;
    remember(key, choice);
    markSkin(choice);
    resetVariant(id, choice === "gallery");
  }

  // (v5.44.45–46) The side of a look just chosen: its own (dark or light), «как у галереи» — the gallery's; a side
  // chosen before for this page is forgotten (prefs: "" drops it).  The switch shows only for looks with both sides.
  function resetVariant(id, follows) {
    const item = document.querySelector(`.skin-item[data-skin="${CSS.escape(id)}"]`);
    const toggles = !!item && item.dataset.toggle === "1";
    const own = (item && item.dataset.variant) || "light";
    const side = follows && toggles && root.dataset.galleryVariant ? root.dataset.galleryVariant : own;
    showVariant(side, toggles);
    if ((root.dataset.skinKey || "skin") === "skin") root.dataset.galleryVariant = side;
    remember(root.dataset.variantKey || "skin_variant", "");
  }

  function showVariant(side, toggles) {
    root.dataset.variant = side;
    const control = document.querySelector(".skin-variant");
    if (!control) return;
    if (toggles !== undefined) control.hidden = !toggles;
    control.querySelectorAll(".skin-vbtn[data-variant]").forEach((b) => {
      const on = b.dataset.variant === side;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  // «Это оформление — на всех страницах»: the gallery gets it, the other pages look like the gallery.
  async function skinForAll() {
    const id = root.dataset.skin;
    const side = root.dataset.variant || "light";        // (v5.44.46) every page gets this side too
    try {
      await postJson("/api/prefs", { looks_all: id, skin_variant: side });
    } catch (err) {
      toast("Не сохранилось: " + err.message, true);
      return;
    }
    root.dataset.gallerySkin = id;
    root.dataset.galleryVariant = side;
    const follow = document.querySelector(".skin-follow");
    const chosen = document.querySelector(`.skin-item[data-skin="${CSS.escape(id)}"]`);
    if (follow && chosen) {
      follow.querySelector(".swatches").innerHTML = chosen.querySelector(".swatches").innerHTML;
      follow.querySelector("small").textContent = `Сейчас там «${chosen.querySelector("b").textContent}»`;
    }
    markSkin((root.dataset.skinKey || "skin") === "skin" ? id : "gallery");
    toast("Это оформление теперь на всех страницах");
  }

  // ── the tag drawer (looks that hide the tag column) ──────────────────────
  function setDrawer(open) {
    root.classList.toggle("drawer-open", open);
    const backdrop = document.querySelector(".drawer-backdrop");
    if (backdrop) backdrop.hidden = !open;
    const toggle = document.querySelector(".drawer-toggle");
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const close = document.querySelector(".drawer-close");
      if (close && getComputedStyle(close).display !== "none") close.focus();
    } else if (toggle && document.activeElement && document.activeElement.closest(".sidebar")) {
      toggle.focus();
    }
  }

  // ── small helpers ────────────────────────────────────────────────────────
  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok || data.ok === false) {
      const error = new Error(data.error || ("Сервер ответил " + response.status));
      error.data = data;                       // the whole answer (e.g. which field a form must fix)
      throw error;
    }
    return data;
  }

  function statusFor(el) {
    // the status line of the button's own block (a sidebar box, a row of tool buttons, a form)
    const box = el.closest(".box, .tool-actions, .select-bar, form, dialog");
    return box ? box.querySelector(".action-status") : null;
  }

  // Text to the clipboard: true when it is there.  First the old way — a hidden text field and «copy»: the program's
  // window lets pages do that, while its clipboard API is not given to them (asked for it, the window never answered
  // before v5.23.1, and «Скопировать» waited forever).  The API only when that fails, and not for ever.
  async function copyText(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.setAttribute("aria-hidden", "true");
    area.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;";
    const focused = document.activeElement;
    document.body.appendChild(area);
    area.select();
    let done = false;
    try { done = document.execCommand("copy"); } catch (_) { done = false; }
    area.remove();
    if (focused && focused !== document.body && focused.focus) focused.focus({ preventScroll: true });
    if (done) return true;
    if (!navigator.clipboard || !navigator.clipboard.writeText) return false;
    try {
      await Promise.race([navigator.clipboard.writeText(text),
        new Promise((_, refuse) => { setTimeout(() => refuse(new Error("no answer")), 3000); })]);
      return true;
    } catch (_) {
      return false;
    }
  }

  // A short message: next to the button when it has a status line, else a note in the corner.
  function say(el, text, isError) {
    const status = (el && statusFor(el)) || document.querySelector(".action-status");
    if (status) {
      status.textContent = text;
      status.style.color = isError ? "var(--danger)" : "";
      return;
    }
    toast(text, isError);
  }

  // A note in the corner of the window (a status line in a sidebar may be out of sight).
  function toast(text, isError) {
    let note = document.querySelector(".toast");
    if (!note) {
      note = document.createElement("div");
      note.className = "toast";
      note.setAttribute("role", "status");
      document.body.appendChild(note);
    }
    note.textContent = text;
    note.classList.toggle("error", !!isError);
    note.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { note.hidden = true; }, 3500);
  }

  // The program window (Qt WebEngine) can do more: its own video player, the clipboard with files,
  // dragging files out with their tags.
  const inApp = navigator.userAgent.includes("QtWebEngine");
  if (inApp) root.dataset.app = "qt";

  async function appRequest(name, body) {
    return postJson("/api/app/" + name, body || {});
  }

  function typing(target) {
    if (!target) return false;
    const tag = target.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
  }

  // The same page once more, as it is now: (v5.44.55) by a «replace» it gives way softly (a reload just jumps); an
  // address with #… would only scroll — that one is reloaded.
  function showAgain() {
    if (location.hash) location.reload();
    else location.replace(location.href);
  }

  // ── buttons ──────────────────────────────────────────────────────────────
  const actions = {
    "skin-menu": () => openSkinMenu(skinMenu().querySelector(".skin-list").hidden),

    skin(el) {
      applySkin(el.dataset.skin);
      openSkinMenu(false);
      const button = skinMenu().querySelector(".skin-button");
      if (button) button.focus();
    },

    // (v5.44.45) the light / dark switch of this page's look — just flips the attribute, the stylesheet has both sides
    variant(el) {
      const value = el.dataset.variant;
      showVariant(value);
      if ((root.dataset.skinKey || "skin") === "skin") root.dataset.galleryVariant = value;
      remember(root.dataset.variantKey || "skin_variant", value);
    },

    "skin-all"() {
      openSkinMenu(false);
      skinForAll();
      const button = skinMenu().querySelector(".skin-button");
      if (button) button.focus();
    },

    // (v5.44.55) «Плавные переходы» — on every page (static/motion.js reads it on the next page; this one at once)
    motion(el) {
      const on = el.getAttribute("aria-checked") !== "true";
      el.setAttribute("aria-checked", on ? "true" : "false");
      const said = el.querySelector("small");
      if (said) said.textContent = on ? "включены — страницы и картинки появляются мягко" : "выключены — всё сменяется сразу";
      root.dataset.motionPref = on ? "on" : "off";
      root.dataset.motion = on && !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) ? "on" : "off";
      remember("motion", on ? "on" : "off");
    },

    drawer: () => setDrawer(!root.classList.contains("drawer-open")),

    "thumb-size"(el) {
      const size = el.dataset.size;
      root.dataset.thumb = size;
      remember("thumbs", size);
      el.parentElement.querySelectorAll("[data-size]").forEach((b) => {
        const on = b === el;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      requestAnimationFrame(() => fitPageCount(true));    // other cells: another number of pictures fits the window
    },

    "toggle-fit": (el, event) => {
      const img = postPicture();
      if (!img) return;
      if (!img.classList.contains("fit")) { setZoom(0); return; }   // full → fit back to the window
      // fit → full: show the full size at the very spot the click landed (a click on the button — the centre)
      const onImg = !!(event && event.target === img);
      const rect = img.getBoundingClientRect();
      const fx = onImg && rect.width ? (event.clientX - rect.left) / rect.width : 0.5;
      const fy = onImg && rect.height ? (event.clientY - rect.top) / rect.height : 0.5;
      zoomToPoint(fx, fy, onImg ? event : null);
    },

    "fit-always-original": () => {
      remember("post_fit", "original");
      document.body.dataset.postFit = "original";
      setZoom(1);
    },

    "fit-always-fit": () => {
      remember("post_fit", "fit");
      document.body.dataset.postFit = "fit";
      setZoom(0);
    },

    "fit-note-off": () => {
      remember("fit_note", "off");
      document.body.dataset.fitNote = "off";
      fitPicture();
    },

    "page-jump": (el) => jumpToPage(el.closest(".page-jump")),

    "nv-toggle": () => { if (nativeVideo) nativeVideo.toggle(); },
    "nv-resume": () => { if (nativeVideo) nativeVideo.resumeHere(); },
    "nv-restart": () => { if (nativeVideo) nativeVideo.restart(); },

    "nv-mute": () => { if (nativeVideo) nativeVideo.mute(); },

    // ⛶: this same player over the whole screen (and back)
    async "nv-full"(el) {
      try {
        if (nativeVideo) nativeVideo.fullscreen();
        else await appRequest("play-video", { id: Number(el.dataset.id) });
      } catch (err) {
        say(el, err.message, true);
      }
    },

    // the video did not open here: the window's own player (mpv where it is installed) instead
    async "nv-player"(el) {
      try {
        if (nativeVideo) await nativeVideo.inPlayer();
        else await appRequest("play-video", { id: Number(el.dataset.id) });
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async rate(el) {
      const id = el.dataset.id;
      const value = Number(el.dataset.value || 0);
      try {
        await postJson("/api/post/" + id + "/rating", { value });
        el.closest(".stars").querySelectorAll(".star").forEach((star, index) => {
          const n = index + 1;
          star.classList.toggle("on", n <= value);
          star.dataset.value = String(n === value ? 0 : n);
        });
        say(el, value ? "Оценка: " + value : "Оценка снята");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async favorite(el) {
      const value = Number(el.dataset.value || 0);
      try {
        await postJson("/api/post/" + el.dataset.id + "/favorite", { value });
        el.dataset.value = value ? "0" : "1";
        el.textContent = value ? "Убрать из избранного" : "В избранное";
        say(el, value ? "Добавлено в избранное" : "Убрано из избранного");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async "open-external"(el) {
      try {
        await postJson("/api/post/" + el.dataset.id + "/open");
        say(el, "Открыто в программе Windows");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async reveal(el) {
      try {
        await postJson("/api/post/" + el.dataset.id + "/reveal");
        say(el, "Папка открыта");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async copy(el) {
      if (await copyText(el.dataset.copy || "")) say(el, "Скопировано: " + (el.dataset.what || "текст"));
      else say(el, "Не получилось скопировать — выделите текст и нажмите Ctrl+C", true);
    },

    async "reveal-manga"(el) {
      try {
        await postJson("/api/manga/" + el.dataset.id + "/reveal");
        say(el, "Папка открыта");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async "choose-manga-folder"(el) {
      try {
        await postJson("/api/app/choose-manga-folder");
        say(el, "Выберите папку в открывшемся окне");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async "play-native"(el) {
      try {
        await appRequest("play-video", { id: Number(el.dataset.id) });
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async "copy-file"(el) {
      try {
        await appRequest("copy-file", { id: Number(el.dataset.id) });
        say(el, "Файл скопирован — его можно вставить в папку или в чат");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async "export-results"(el) {
      try {
        await appRequest("export-results", {
          tags: el.dataset.tags || "", section: el.dataset.section || "all", source: el.dataset.source || "all",
        });
        say(el, "Выберите, куда сохранить файл");
      } catch (err) {
        say(el, err.message, true);
      }
    },

    async "manga-strip"(el) {
      try {
        await postJson("/api/prefs", { manga_strip: el.dataset.place });
        location.reload();
      } catch (err) {
        say(el, err.message, true);
      }
    },

    notes(el) {
      const layer = document.querySelector(".notes");
      if (!layer) return;
      layer.hidden = !layer.hidden;
      el.setAttribute("aria-pressed", layer.hidden ? "false" : "true");
    },

    async trash(el) {
      // The old gallery asked before deleting: here the button asks — a second press within 4 s.
      if (!el.classList.contains("confirming")) {
        el.classList.add("confirming");
        el.dataset.label = el.textContent;
        el.textContent = "Точно в корзину? Нажмите ещё раз";
        clearTimeout(el.confirmTimer);
        el.confirmTimer = setTimeout(() => {
          el.classList.remove("confirming");
          el.textContent = el.dataset.label;
        }, 4000);
        return;
      }
      clearTimeout(el.confirmTimer);
      el.classList.remove("confirming");
      el.textContent = el.dataset.label;
      el.disabled = true;
      if (nativeVideo) nativeVideo.stop();       // the program's player lets go of the file first
      // A playing <video> keeps its file open, and Windows cannot move an open file.
      const videos = Array.from(document.querySelectorAll("video.post-media[src]"));
      videos.forEach((video) => {
        video.dataset.src = video.getAttribute("src");
        video.pause();
        video.removeAttribute("src");
        video.load();
      });
      try {
        await postJson("/api/post/" + el.dataset.id + "/trash");
        el.dataset.action = "restore";
        el.classList.remove("danger");
        el.textContent = "Вернуть из корзины";
        say(el, "Перемещено в корзину");
      } catch (err) {
        videos.forEach((video) => { video.setAttribute("src", video.dataset.src); });
        say(el, err.message, true);
      } finally {
        el.disabled = false;
      }
    },

    async restore(el) {
      el.disabled = true;
      try {
        await postJson("/api/post/" + el.dataset.id + "/restore");
        el.dataset.action = "trash";
        el.classList.add("danger");
        el.textContent = "Удалить в корзину";
        say(el, "Возвращено из корзины");
      } catch (err) {
        say(el, err.message, true);
      } finally {
        el.disabled = false;
      }
    },

    // (v5.44.53) «SFW / Всё / NSFW» in the header (webui/content_mode.py): the page is shown again in the new mode,
    // or — when it is hidden there — the list it belongs to (the program's first page)
    async "content-mode"(el) {
      const mode = el.dataset.mode;
      if (!mode || root.dataset.contentMode === mode) return;
      document.querySelectorAll(".mode-switch [data-mode]").forEach((b) => {
        const on = b.dataset.mode === mode;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      root.dataset.contentMode = mode;
      try {
        const answer = await postJson("/api/content-mode", { mode, path: location.pathname + location.search });
        if (answer.go) location.assign(answer.go);
        else showAgain();
      } catch (err) {
        toast("Режим не переключился: " + err.message, true);
      }
    },

    // «Это хентай» / «Это не хентай» on a title's page
    async "content-mark"(el) {
      el.disabled = true;
      try {
        const answer = await postJson("/api/content-mark", {
          kind: el.dataset.kind, id: el.dataset.id, hentai: el.dataset.value === "hentai" });
        if (answer.go) location.assign(answer.go);
        else showAgain();
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },
  };

  document.addEventListener("click", (event) => {
    const el = event.target.closest("[data-action]");
    const menu = skinMenu();
    if (menu && !menu.contains(event.target)) openSkinMenu(false);
    if (!el || !actions[el.dataset.action]) return;
    event.preventDefault();
    actions[el.dataset.action](el, event);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const menu = skinMenu();
    if (menu && !menu.querySelector(".skin-list").hidden) {
      openSkinMenu(false);
      menu.querySelector(".skin-button").focus();
    } else if (root.classList.contains("drawer-open")) {
      setDrawer(false);
    }
  });

  // Up/down arrows move through the look menu.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const item = event.target.closest && event.target.closest(".skin-item");
    if (!item) return;
    event.preventDefault();
    const items = Array.from(document.querySelectorAll(".skin-item"));
    const next = items[(items.indexOf(item) + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length];
    next.focus();
  });

  // ── arrow keys: previous / next post or page ─────────────────────────────
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (typing(event.target)) return;
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (root.classList.contains("drawer-open") || (event.target.closest && event.target.closest(".skin-menu"))) return;
    const link = document.querySelector('a[data-key="' + event.key + '"]');
    if (link) {
      event.preventDefault();
      window.location.href = link.href;
    }
  });

  // ── a card's picture fitted to the window (all of it seen without scrolling) ──
  // The picture takes the column's width and the window's height below its top; a long strip (much taller than
  // wide) only the width — it is read by scrolling.  The «r34» look says so in a box above it, as rule34 does
  // («Картинка уменьшена под окно»), with the way to the full size and to keep it so.
  function postPicture() {
    return document.querySelector(".post-stage img.post-media");
  }

  function fitPicture() {
    const img = postPicture();
    if (!img) return;
    const stage = img.closest(".post-stage");
    const column = img.closest(".post-view") || stage.parentElement;
    // a video's frames come at the size they are shown at: its own proportions are the card's (or the poster's)
    const video = img.classList.contains("nv-frame");
    const w = (video ? Number(img.getAttribute("width")) || Number(img.dataset.w) : 0) || img.naturalWidth ||
      Number(img.getAttribute("width")) || 0;
    const h = (video ? Number(img.getAttribute("height")) || Number(img.dataset.h) : 0) || img.naturalHeight ||
      Number(img.getAttribute("height")) || 0;
    const tall = w > 0 && h / w > 2.2;
    const box = getComputedStyle(stage);
    const px = (v) => parseFloat(v) || 0;
    const across = px(box.paddingLeft) + px(box.paddingRight) + px(box.borderLeftWidth) + px(box.borderRightWidth);
    const bar = video ? img.closest(".native-video").querySelector(".nv-bar") : null;
    const under = px(box.paddingBottom) + px(box.borderBottomWidth) + (bar ? bar.offsetHeight : 0);
    let width = 0;
    const place = () => {
      // from the top of the stage's inside: a look that centres the picture in a tall stage gives it all of that
      const inside = stage.getBoundingClientRect().top + px(box.paddingTop) + px(box.borderTopWidth);
      const top = Math.min(img.getBoundingClientRect().top, inside) + window.scrollY;
      const room = Math.max(160, Math.floor(window.innerHeight - top - under - 14));
      width = Math.max(1, Math.floor(column.clientWidth - across));
      if (w > 0 && h > 0) {
        width = Math.min(width, w);
        if (!tall) width = Math.min(width, Math.floor(room * w / h));
        stage.style.setProperty("--fit-w", Math.max(40, width) + "px");
      }
      stage.style.setProperty("--fit-h", tall ? "none" : room + "px");
    };
    const note = document.querySelector(".resize-note");
    if (note) note.hidden = true;
    place();
    // decided from the card's size before the picture arrives: its place does not change when it comes
    if (!note || !w || !h) return;
    const fitted = img.classList.contains("fit");
    const scaled = fitted && w > width + 1;
    const big = !fitted && document.body.dataset.postFit === "original" &&
      (w > column.clientWidth - across + 1 || h > window.innerHeight);
    if (document.body.dataset.fitNote === "off" || !(scaled || big)) return;
    note.dataset.mode = scaled ? "scaled" : "full";
    note.hidden = false;
    place();
  }

  function pictureShown(img) {
    // the first look at a decoded picture: in its own size when chosen so, else fitted
    if (img !== postPicture()) return;
    if (document.body.dataset.postFit === "original" && !pictureShown.done) {
      pictureShown.done = true;
      setZoom(1);
      return;
    }
    fitPicture();
  }

  if (postPicture()) {
    fitPicture();                                     // before it arrives: its place (width and height of the card)
    let resizing = 0;
    window.addEventListener("resize", () => {
      clearTimeout(resizing);
      resizing = setTimeout(fitPicture, 120);
    });
  }

  // ── page numbers: a field for a page ──
  function jumpToPage(box) {
    const input = box && box.querySelector("input");
    if (!input) return;
    const pages = Number(input.max) || 1;
    let n = Math.round(Number(input.value));
    if (!n) return;
    n = Math.max(1, Math.min(pages, n));
    const first = new URL(input.dataset.first, location.href);
    const last = new URL(input.dataset.last, location.href);
    let key = "page";                          // the parameter that numbers the pages of this list
    for (const [k, v] of last.searchParams) {
      if (v === String(pages) && first.searchParams.get(k) !== v) { key = k; break; }
    }
    if (n === 1) first.searchParams.delete(key); else first.searchParams.set(key, String(n));
    location.href = first.toString();
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.closest && event.target.closest(".page-jump")) {
      event.preventDefault();
      jumpToPage(event.target.closest(".page-jump"));
    }
  });

  // ── as many pictures on a page as fit the window ──
  // Columns × rows of the look and the thumbnail size, from the top of the pictures to the page numbers at the
  // bottom of the window.  The number goes into a cookie of this browser (the server reads it, webui/pages/
  // gallery.py ``_per_page``); when it differs from the page's, the page is asked again with the same first
  // picture.  A count that keeps changing right after such a reload is left alone (no endless reloads).
  const FIT_COOKIE = "lb_fit_pp";

  function countFitting() {
    const grid = document.querySelector(".thumbs");
    if (!grid || !grid.children.length) return 0;
    const cs = getComputedStyle(grid);
    const cells = Array.from(grid.children);
    let cols = 0;
    if (cs.display.includes("grid") && cs.gridTemplateColumns && cs.gridTemplateColumns !== "none") {
      cols = cs.gridTemplateColumns.split(" ").filter(Boolean).length;
    }
    if (!cols) {
      const top0 = cells[0].offsetTop;
      cols = cells.filter((c) => Math.abs(c.offsetTop - top0) < 2).length;
    }
    const gap = parseFloat(cs.rowGap) || 0;
    let pitch = 0;
    const row = parseFloat(cs.getPropertyValue("--row")) || 0;
    if (cs.display.includes("flex") && row > 0) {
      // rows that take their width from the pictures («Лента»): how many go in a row depends on the pictures, so
      // the count is the same estimate on every page (pictures a third wider than tall, rows a little stretched)
      const across = parseFloat(cs.columnGap) || 0;
      cols = Math.max(1, Math.floor((grid.clientWidth + across) / (row * 4 / 3 + across)));
      pitch = row * 1.1 + gap;
    } else {
      if (cells.length > cols && cells[cols]) pitch = cells[cols].offsetTop - cells[0].offsetTop;
      if (!(pitch > 0)) pitch = cells[0].getBoundingClientRect().height + gap;
    }
    if (!(pitch > 0) || !cols) return 0;
    const top = grid.getBoundingClientRect().top + window.scrollY;
    // the page numbers' line, the same on every page (a page whose numbers wrap scrolls a little rather than
    // making the count differ from page to page — and the pages ask each other again and again)
    const pager = document.querySelector(".paginator");
    const link = pager && pager.querySelector("a, b");
    const below = link ? link.getBoundingClientRect().height + (parseFloat(getComputedStyle(pager).marginTop) || 0) + 8 : 46;
    const room = window.innerHeight - top - below - 10;
    const rows = Math.max(1, Math.floor((room + gap) / pitch));
    return Math.max(2, Math.min(400, cols * rows));
  }

  function fitPageCount(asked) {
    const body = document.body;
    if (body.dataset.fitPage !== "1") return;
    const fit = countFitting();
    const per = Number(body.dataset.perPage) || 0;
    if (!fit || !per || fit === per) return;
    document.cookie = FIT_COOKIE + "=" + fit + "; path=/; max-age=31536000; SameSite=Strict";
    const total = Number(body.dataset.total) || 0;
    if (total <= Math.min(fit, per)) return;          // all of them are on this page either way
    // A count that goes back and forth right after a reload (the same look, size, window and search) is left as it
    // is: the page asked once is enough.
    const here = new URL(location.href);
    here.searchParams.delete("page");
    const key = [root.dataset.skin, root.dataset.thumb, window.innerWidth, window.innerHeight, here.search].join("|");
    let last = null;
    try { last = JSON.parse(sessionStorage.getItem("lb-fit-last") || "null"); } catch (_) { last = null; }
    if (!asked && last && last.key === key && Date.now() - last.t < 4000 && last.to === per && last.from === fit) return;
    try {
      sessionStorage.setItem("lb-fit-last", JSON.stringify({ key, from: per, to: fit, t: Date.now() }));
    } catch (_) { /* fine */ }
    const page = Number(body.dataset.page) || 1;
    const target = new URL(location.href);
    const next = Math.floor(((page - 1) * per) / fit) + 1;       // the page with the same first picture
    if (next > 1) target.searchParams.set("page", String(next)); else target.searchParams.delete("page");
    location.replace(target.toString());
  }

  // Rows that take their width from the pictures (the look «Лента»): a last row about as long as the others is
  // stretched to the edge like them; a short one (the end of the results) is left as it is.
  function stretchLastRow() {
    const grid = document.querySelector(".thumbs");
    if (!grid || !getComputedStyle(grid).display.includes("flex")) return;
    const cells = Array.from(grid.children);
    if (cells.length < 2) return;
    grid.classList.remove("full-last");
    const top0 = cells[0].offsetTop;
    const first = cells.filter((c) => Math.abs(c.offsetTop - top0) < 2).length;
    const lastTop = cells[cells.length - 1].offsetTop;
    const last = cells.filter((c) => Math.abs(c.offsetTop - lastTop) < 2).length;
    if (lastTop > top0 && last >= Math.ceil(first * 0.6)) grid.classList.add("full-last");
  }
  stretchLastRow();
  window.addEventListener("resize", () => requestAnimationFrame(stretchLastRow));

  if (document.body.dataset.fitPage === "1") {
    fitPageCount(false);
    let resizing = 0;
    window.addEventListener("resize", () => {
      clearTimeout(resizing);
      resizing = setTimeout(() => fitPageCount(false), 450);
    });
  }

  // ── lists of covers paged by whole rows (manga, the series and long videos of «Видео») ──
  // About the list's usual number on a page (data-rows: 25 as on nhentai), as many whole rows of the columns this
  // window has as come nearest to it — 25 covers in six columns left one alone in the last row.  The number goes
  // into the list's cookie (data-rows-cookie: the server pages by it, webui/html.py ``rows_per_page``), and a page
  // made for another number is asked again with the same first item (data-page; a shelf without pages — the same
  // address).  Numbers that go back right after such a reload are left as they are (no endless reloads).
  function rowColumns(grid) {
    const cs = getComputedStyle(grid);
    if (cs.display.includes("grid") && cs.gridTemplateColumns && cs.gridTemplateColumns !== "none") {
      const tracks = cs.gridTemplateColumns.split(" ").filter(Boolean).length;      // auto-fill keeps empty tracks
      if (tracks) return tracks;
    }
    if (cs.display.includes("flex") && (parseFloat(cs.getPropertyValue("--row")) || 0) > 0) return 0;  // «Лента»
    const cells = Array.from(grid.children);
    if (cells.length < 2) return 0;
    const top0 = cells[0].offsetTop;
    const first = cells.filter((c) => Math.abs(c.offsetTop - top0) < 2).length;
    return first < cells.length ? first : 0;           // one row of all of them: how many more would fit is unknown
  }

  function fitRows() {
    const changes = [];
    let page = 0;
    for (const grid of document.querySelectorAll("[data-rows]")) {
      const usual = Number(grid.dataset.rows) || 0;
      const cookie = grid.dataset.rowsCookie || "";
      const cols = usual && /^[a-z_]+$/.test(cookie) ? rowColumns(grid) : 0;
      if (!cols) continue;
      const want = Math.max(4, Math.min(240, cols * Math.max(1, Math.round(usual / cols))));
      document.cookie = cookie + "=" + want + "; path=/; max-age=31536000; SameSite=Strict";
      const per = Number(grid.dataset.perPage) || 0;
      const total = Number(grid.dataset.total) || 0;
      if (!per || want === per || total <= Math.min(want, per)) continue;
      changes.push(cookie + ":" + per + ">" + want);
      if (grid.dataset.page) page = Math.floor(((Number(grid.dataset.page) || 1) - 1) * per / want) + 1;
    }
    if (!changes.length) return;
    const here = new URL(location.href);
    here.searchParams.delete("page");
    const key = [root.dataset.skin, window.innerWidth, here.pathname, here.search].join("|");
    let last = null;
    try { last = JSON.parse(sessionStorage.getItem("lb-rows-last") || "null"); } catch (_) { last = null; }
    const back = changes.map((c) => c.replace(/:(\d+)>(\d+)$/, ":$2>$1")).join(",");
    if (last && last.key === key && Date.now() - last.t < 4000 && last.back === changes.join(",")) return;
    try {
      sessionStorage.setItem("lb-rows-last", JSON.stringify({ key, back, t: Date.now() }));
    } catch (_) { /* fine */ }
    const target = new URL(location.href);
    if (page > 1) target.searchParams.set("page", String(page));
    else if (page === 1) target.searchParams.delete("page");
    location.replace(target.toString());
  }

  if (document.querySelector("[data-rows]")) {
    fitRows();
    let rowsResizing = 0;
    window.addEventListener("resize", () => {
      clearTimeout(rowsResizing);
      rowsResizing = setTimeout(fitRows, 450);
    });
  }

  // ── the picture of the post page (and of the manga reader): shown when it is all there ──
  // A big picture drawn while it still arrives goes in stripes (a progressive JPEG comes as blocks first), and on
  // some graphics drivers such a half-drawn frame — even with a strip of the previous page in it — stayed on the
  // screen.  So the picture stays hidden (its thumbnail stands in) until it has come whole and is decoded; then it
  // is drawn once more a moment later.  An answer that came short, a picture that does not open, or one unlike its
  // own thumbnail in some rows (bytes of another file in it) is asked for again — twice at most, then the page
  // says so and offers to try again.
  const TROUBLE_SAY = {
    short: "Картинка пришла не целиком — загружаю заново",
    error: "Картинка не открылась — загружаю заново",
    splice: "Картинка пришла испорченной — загружаю заново",
  };

  function drawAgainSoon(img) {
    // a fresh frame of the picture after the one that showed it (a stale frame does not stay on the screen)
    [150, 700].forEach((ms) => setTimeout(() => {
      img.style.opacity = "0.999";
      requestAnimationFrame(() => requestAnimationFrame(() => { img.style.opacity = ""; }));
    }, ms));
  }

  function bodySize(img) {
    // how many bytes of the picture came (-1: the browser does not say — kept in its memory, say — or the server
    // answered with an error page, which is not the picture at all)
    try {
      const list = performance.getEntriesByName(img.currentSrc || img.src);
      const entry = list[list.length - 1];
      if (!entry || (entry.responseStatus && entry.responseStatus >= 400)) return -1;
      return entry.encodedBodySize > 0 ? entry.encodedBodySize : -1;
    } catch (_) {
      return -1;
    }
  }

  function rowsUnlike(img, thumb) {
    // how far each row of the picture is from the same row of its thumbnail (0..255), both drawn small;
    // null when they cannot be compared (no thumbnail, other proportions — turned by EXIF, say)
    if (!thumb || !thumb.complete || !thumb.naturalWidth || !img.naturalWidth) return null;
    const ratio = img.naturalWidth / img.naturalHeight;
    if (Math.abs(ratio - thumb.naturalWidth / thumb.naturalHeight) > 0.04 * ratio) return null;
    const w = 16;
    const h = Math.max(8, Math.min(40, Math.round(w / ratio)));
    const grab = (source) => {
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const g = canvas.getContext("2d", { willReadFrequently: true });
      g.imageSmoothingQuality = "medium";       // an average of the rows, not a few pixels picked out of them
      g.drawImage(source, 0, 0, w, h);
      return g.getImageData(0, 0, w, h).data;
    };
    let a, b;
    try {
      a = grab(img);
      b = grab(thumb);
    } catch (_) {
      return null;
    }
    const rows = [];
    for (let y = 0; y < h; y++) {
      let sum = 0, n = 0;
      for (let x = 0; x < w; x++) {
        const k = (y * w + x) * 4;
        if (a[k + 3] < 250) continue;          // see-through: the thumbnail has some background there
        sum += Math.abs(a[k] - b[k]) + Math.abs(a[k + 1] - b[k + 1]) + Math.abs(a[k + 2] - b[k + 2]);
        n += 3;
      }
      rows.push(n ? sum / n : 0);
    }
    return rows;
  }

  function looksSpliced(rows) {
    // a band of rows far from the thumbnail while much of the picture matches it; a picture unlike its thumbnail
    // everywhere (other colours, a CMYK JPEG) is not a broken transfer
    if (!rows || !rows.length) return false;
    const bad = rows.filter((v) => v > 48).length;
    const good = rows.filter((v) => v < 18).length;
    return bad >= 2 && good >= rows.length / 4;
  }

  function setupPicture(img, holder, problem, id) {
    const base = img.getAttribute("src");
    const bytes = Number(img.dataset.bytes || 0);
    const thumb = holder.querySelector(".post-ph");
    const fresh = () => base + (base.includes("?") ? "&" : "?") + "r=" + Date.now();
    let tries = 0;            // times the picture was asked for again
    let looked = false;       // the file was looked at once by itself (does it come whole? does it open?)
    let ticket = 0;
    let failed = false;

    function report(kind, detail) {
      if (!id) return;
      postJson("/api/post/" + id + "/picture-trouble", Object.assign({ kind, tries }, detail || {})).catch(() => {});
    }
    function giveUp(kind) {
      holder.classList.remove("loading");
      img.hidden = true;
      if (thumb) thumb.hidden = true;
      if (problem) {
        const line = problem.querySelector("p");
        line.textContent = kind === "format"
          ? "Файл пришёл целиком, но встроенный просмотр его не показывает (формат не поддерживается или файл повреждён)."
          : "Картинка не загрузилась.";
        problem.hidden = false;
        if (kind !== "format" && holder.dataset.why) {
          // a picture from a site (a part of a сборка the library has not): what the site answered
          fetch(base, { cache: "no-store", credentials: "same-origin" })
            .then((response) => (response.ok ? null : response.json()))
            .then((answer) => { if (answer && answer.error) line.textContent = "Картинка не загрузилась: " + answer.error + "."; })
            .catch(() => {});
        }
      }
      report(kind === "format" ? "format" : "gave_up");
    }
    function load(src) {
      holder.classList.add("loading");          // the thumbnail stands in again meanwhile
      img.src = src;
      wait();
    }
    function again(kind) {
      if (tries >= 2) {
        giveUp(kind);
        return;
      }
      tries += 1;
      toast(TROUBLE_SAY[kind] || TROUBLE_SAY.error);
      load(fresh());
    }
    async function lookAtFile(mine) {
      // The picture did not open: the transfer, or the file itself?  One look at the file tells.  It comes whole and
      // opens — shown from what came; it comes whole and does not open — the built-in view cannot show it (asking
      // again would not help); it does not come whole — asked for again.
      looked = true;
      let blob = null;
      try {
        const response = await fetch(fresh(), { cache: "no-store", credentials: "same-origin" });
        if (response.ok) blob = await response.blob();
      } catch (_) {
        blob = null;
      }
      if (mine !== ticket) return;
      if (blob && (!bytes || blob.size === bytes)) {
        let opens = true;
        try {
          (await createImageBitmap(blob)).close();
        } catch (_) {
          opens = false;
        }
        if (mine !== ticket) return;
        if (!opens) {
          giveUp("format");
          return;
        }
        report("error", { got: blob.size, bytes });
        tries += 1;
        toast(TROUBLE_SAY.error);
        load(URL.createObjectURL(blob));
        return;
      }
      report(blob ? "short" : "error", { got: blob ? blob.size : -1, bytes });
      again(blob ? "short" : "error");
    }
    function show() {
      holder.classList.remove("loading");
      pictureShown(img);
      drawAgainSoon(img);
      if (thumb && tries < 2) {
        setTimeout(() => {
          const rows = rowsUnlike(img, thumb);
          if (!looksSpliced(rows)) return;
          report("splice", { rows: rows.map(Math.round) });
          again("splice");
        }, 250);
      }
    }
    function short() {
      const got = bodySize(img);
      return bytes > 0 && got > 0 && got < bytes ? got : 0;
    }
    function wait() {
      const mine = ++ticket;
      failed = false;
      img.decode().then(() => {
        if (mine !== ticket) return;
        const got = short();
        if (got) {
          report("short", { got, bytes });
          again("short");
          return;
        }
        show();
      }, () => {
        // The «error» event, if any, comes around the same moment: decide a moment later.  How much came says
        // nothing here — the browser stops taking a file it cannot decode — so the file is looked at by itself.
        setTimeout(() => {
          if (mine !== ticket) return;
          if (!failed && img.complete && img.naturalWidth) {
            show();                              // too big to decode ahead: it is drawn the usual way
          } else if (!looked) {
            lookAtFile(mine);
          } else {
            report("error", { got: bodySize(img), bytes });
            again("error");
          }
        }, 0);
      });
    }
    img.addEventListener("error", () => { failed = true; });
    if (problem) {
      problem.addEventListener("click", (event) => {
        if (!event.target.closest('[data-action="picture-retry"]')) return;
        problem.hidden = true;
        img.hidden = false;
        if (thumb) thumb.hidden = false;
        tries = 0;
        looked = false;
        load(fresh());
      });
    }
    wait();
  }

  document.querySelectorAll(".media-wrap.loading img.post-media, .reader-stage.loading img.reader-image").forEach((img) => {
    const holder = img.closest(".media-wrap, .reader-stage");
    const problem = holder.nextElementSibling && holder.nextElementSibling.matches(".picture-problem")
      ? holder.nextElementSibling : null;
    setupPicture(img, holder, problem, holder.dataset.id || "");
  });

  // ── tag hints in the search box ─────────────────────────────────────────
  function currentWord(input) {
    const caret = input.selectionStart == null ? input.value.length : input.selectionStart;
    const before = input.value.slice(0, caret);
    const start = Math.max(before.lastIndexOf(" "), before.lastIndexOf("/")) + 1;
    let end = input.value.slice(caret).search(/[\s/]/);
    end = end < 0 ? input.value.length : caret + end;
    let word = input.value.slice(start, end);
    let from = start;
    if (word.startsWith("-") && input.value[start - 1] !== "/") {
      word = word.slice(1);
      from += 1;
    }
    return { word, from, to: end };
  }

  function setupAutocomplete(input) {
    const row = input.parentElement;
    const list = document.createElement("ul");
    list.className = "ac-list";
    list.hidden = true;
    list.setAttribute("role", "listbox");
    list.id = "ac-" + Math.random().toString(36).slice(2);
    row.appendChild(list);
    input.setAttribute("aria-controls", list.id);
    input.setAttribute("aria-autocomplete", "list");

    let items = [];
    let active = -1;
    let timer = 0;
    let lastAsked = "";
    let controller = null;

    function close() {
      list.hidden = true;
      items = [];
      active = -1;
      input.removeAttribute("aria-activedescendant");
    }

    function highlight(index) {
      active = index;
      list.querySelectorAll("li").forEach((li, n) => {
        li.classList.toggle("active", n === index);
        li.setAttribute("aria-selected", n === index ? "true" : "false");
      });
      if (index >= 0) {
        const li = list.children[index];
        input.setAttribute("aria-activedescendant", li.id);
        li.scrollIntoView({ block: "nearest" });
      }
    }

    function accept(index) {
      const item = items[index];
      if (!item) return;
      const { from, to } = currentWord(input);
      const value = input.value;
      const tail = value.slice(to).replace(/^\s+/, "");
      const head = value.slice(0, from) + item.tag + " ";
      input.value = head + tail;
      input.setSelectionRange(head.length, head.length);
      close();
      input.focus();
    }

    function render(found) {
      items = found;
      list.textContent = "";
      found.forEach((item, n) => {
        const li = document.createElement("li");
        li.id = list.id + "-" + n;
        li.setAttribute("role", "option");
        li.className = "tag-" + (item.category || "general");
        const name = document.createElement("span");
        name.className = "tag-name";
        name.textContent = item.name.replace(/_/g, " ");
        const count = document.createElement("span");
        count.className = "tag-count";
        count.textContent = item.count;
        li.append(name, count);
        li.addEventListener("mousedown", (event) => {
          event.preventDefault();
          accept(n);
        });
        list.appendChild(li);
      });
      list.hidden = found.length === 0;
      highlight(-1);
    }

    async function ask() {
      const { word } = currentWord(input);
      if (word.length < 1) {
        close();
        return;
      }
      if (word === lastAsked && !list.hidden) return;
      lastAsked = word;
      if (controller) controller.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/api/autocomplete?q=" + encodeURIComponent(word), {
          credentials: "same-origin",
          signal: controller.signal,
        });
        const data = await response.json();
        // an answer that comes after the field was left (a button pressed meanwhile) opens no list
        if (currentWord(input).word === word && document.activeElement === input) render(data.items || []);
      } catch (err) {
        if (err.name !== "AbortError") close();
      }
    }

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(ask, 110);
    });

    input.addEventListener("keydown", (event) => {
      if (list.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        highlight(Math.min(items.length - 1, active + 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        highlight(Math.max(-1, active - 1));
      } else if ((event.key === "Enter" || event.key === "Tab") && active >= 0) {
        event.preventDefault();
        accept(active);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });

    input.addEventListener("blur", () => setTimeout(close, 120));
  }

  document.querySelectorAll("input[data-autocomplete]").forEach(setupAutocomplete);

  // ── manga reader: a click on the left third goes back, elsewhere forward; the page list jumps ──
  document.querySelectorAll(".reader-stage").forEach((stage) => {
    stage.addEventListener("click", (event) => {
      const box = stage.getBoundingClientRect();
      const back = event.clientX - box.left < box.width / 3;
      const target = back ? stage.dataset.prev : stage.dataset.next;
      if (target) window.location.href = target;
    });
  });
  // The strip of manga pages scrolls itself to the current page; the window stays where it is.
  const pageStrip = document.querySelector(".page-strip");
  const currentStripPage = pageStrip && pageStrip.querySelector(".current");
  if (currentStripPage) {
    pageStrip.scrollLeft = currentStripPage.offsetLeft - (pageStrip.clientWidth - currentStripPage.offsetWidth) / 2;
    pageStrip.scrollTop = currentStripPage.offsetTop - (pageStrip.clientHeight - currentStripPage.offsetHeight) / 2;
  }
  document.querySelectorAll('select[data-action="goto-page"]').forEach((select) => {
    select.addEventListener("change", () => {
      window.location.href = select.dataset.base + select.value;
    });
  });

  // ── a video played by the program inside the page ──
  // The window's browser cannot decode H.264: the program plays the video and its frames come as pictures in the
  // poster's place (webui/video_relay.py) — the video stays where the page shows it, scrolls with it, menus open
  // over it.  The bar asks the program to pause, seek and change the sound, and reads the state back.
  // the page before was a player over the whole screen (⛶) and went on to this one (the next episode): this page's
  // player goes on over the whole screen; a page without one gives the window its usual size back at once
  // Another player of the window for this video (the address of this page with it): asked once a page — the
  // address itself says which player it is for, and that page offers the usual player if it cannot play it either.
  let switched = false;
  function switchPlayer(address) {
    if (switched || !inApp || !address) return false;
    switched = true;
    location.replace(address);
    return true;
  }
  const FULL_KEY = "lb-video-full";
  const carriedFull = (() => {
    try {
      const on = sessionStorage.getItem(FULL_KEY) === "1";
      sessionStorage.removeItem(FULL_KEY);
      return on;
    } catch (_) {
      return false;
    }
  })();
  const nativeVideo = (() => {
    // a card's video (data-id) or an episode of the tab «Видео» (data-episode: where to start, played once);
    // (v5.44.106) an episode of «Смотреть» (data-stream: the token of its stream on the program — watch.js sets it)
    const box = document.querySelector(".native-video[data-id], .native-video[data-episode], .native-video[data-stream]");
    if (!box) return null;
    const frame = box.querySelector(".nv-frame");
    const play = box.querySelector(".nv-play");
    const time = box.querySelector(".nv-time");
    const seek = box.querySelector(".nv-seek");
    const mute = box.querySelector(".nv-mute");
    const volume = box.querySelector(".nv-volume");
    const problem = box.querySelector(".nv-problem");
    const tracksRow = box.querySelector(".nv-tracks");
    const audioMenu = box.querySelector(".nv-audio");
    const subsMenu = box.querySelector(".nv-subs");
    const resumeButton = box.querySelector(".nv-resume");
    const restartButton = box.querySelector(".nv-restart");
    const bigButton = box.querySelector(".nv-big");
    const fullButton = box.querySelector(".nv-full");
    const poster = frame.getAttribute("src");
    let token = "";
    let state = {};
    let seeking = false;
    let starting = null;
    let timer = 0;
    // the furthest place reached (what was watched before, then this play): «Продолжить просмотр» when well before it
    let furthest = Math.max(Number(box.dataset.resume) || 0, Number(box.dataset.from) || 0);
    let resumeUsed = false;
    let wasPlaying = false;
    const RESUME_GAP = Number(box.dataset.resumeGap) || 15;
    const RESUME_END = box.dataset.resumeEnd !== undefined ? Number(box.dataset.resumeEnd) || 0 : 10;  // the end credits

    const clock = (value) => {
      const t = Math.max(0, Math.floor(Number(value) || 0));
      const hours = Math.floor(t / 3600), minutes = Math.floor(t / 60) % 60, seconds = t % 60;
      return (hours ? hours + ":" + String(minutes).padStart(2, "0") : String(minutes)) + ":" +
        String(seconds).padStart(2, "0");
    };
    function place() {
      const r = frame.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      return { w: Math.round(r.width * dpr), h: Math.round(r.height * dpr) };
    }
    function showProblem(text) {
      problem.querySelector("p").textContent = text;
      problem.hidden = false;
      box.classList.add("failed");
    }
    function ended() {
      token = "";
      clearTimeout(timer);
      box.classList.remove("started", "playing");
      frame.src = poster;
      play.textContent = "▶";
      if (tracksRow) tracksRow.hidden = true;
      if (resumeButton) resumeButton.hidden = true;
      box.querySelectorAll(".nv-skip").forEach((b) => { b.hidden = true; });     // «Пропустить …»: while it plays
      showSaved();
    }
    // before the play: where it starts — an episode watched before goes on from its place (the bar shows it, the
    // big button says so); «Сначала» — from the beginning instead
    function showSaved() {
      if (token) return;
      const at = Number(box.dataset.from) || 0;
      const length = Number(box.dataset.length) || Number(state.duration) || 0;
      if (at > 0) {
        time.textContent = clock(at) + " / " + (length > 0 ? clock(length) : "–:––");
        if (length > 0) seek.value = String(Math.round(1000 * Math.min(at, length) / length));
      }
      if (!bigButton) return;
      bigButton.classList.toggle("resume", at > 0);
      bigButton.textContent = at > 0 ? "▶ Продолжить с " + clock(at) : "▶";
      bigButton.setAttribute("aria-label", at > 0 ? "Продолжить с " + clock(at) : "Смотреть");
      if (restartButton) restartButton.hidden = !(at > 0);
    }
    function restart() {
      box.dataset.from = "0";
      showSaved();
      if (token) command("seek", { value: 0 });
      else start();
    }
    function start() {
      if (starting) return starting;
      problem.hidden = true;
      box.classList.remove("failed");
      starting = (async () => {
        try {
          if (box.dataset.stream === "") throw new Error("выберите серию");
          const what = box.dataset.episode
            ? { cmd: "start", episode: box.dataset.episode, from: Number(box.dataset.from) || 0,
                loop: box.dataset.loop !== "0" }
            : box.dataset.stream !== undefined
              ? { cmd: "start", stream: box.dataset.stream, from: Number(box.dataset.from) || 0, loop: false }
              : { cmd: "start", id: Number(box.dataset.id) };
          const reply = await appRequest("video", Object.assign(what, place()));
          token = reply.token || "";
          if (!token) throw new Error("видео не запустилось");
          frame.src = "/video/live/" + token;
          box.classList.add("started");
          poll();
        } catch (err) {
          showProblem(err.message);
        } finally {
          starting = null;
        }
      })();
      return starting;
    }
    async function command(cmd, extra) {
      if (!token) {
        await start();
        if (cmd === "toggle" || cmd === "play") return;
        if (!token) return;
      }
      try {
        await appRequest("video", Object.assign({ cmd, token }, extra || {}));
      } catch (err) {
        if (err.data && err.data.over) ended();
        else say(null, err.message, true);
      }
    }
    function render() {
      const width = Number(state.width) || 0, height = Number(state.height) || 0;
      if (width > 0 && height > 0 && (frame.dataset.w !== String(width) || frame.dataset.h !== String(height))) {
        frame.dataset.w = String(width);           // the video's own size: its place on the page, then its frames
        frame.dataset.h = String(height);
        fitPicture();
        command("size", place());
      }
      const playing = !!state.playing;
      if (playing !== wasPlaying) {
        wasPlaying = playing;
        wake();                                  // on the whole screen: the bar shows on a pause, hides while playing
        const at = Number(state.position) || 0;
        if (!playing && at > 1 && !state.ended) box.dataset.from = String(at);   // a later start goes on from here
      }
      if (state.ended) box.dataset.from = "0";
      play.setAttribute("aria-label", playing ? "Пауза" : "Пуск");   // its icon: .native-video.playing (booru.css)
      box.classList.toggle("playing", playing);
      const duration = Number(state.duration) || 0;
      const position = Number(state.position) || 0;
      if (!seeking) {
        time.textContent = clock(position) + " / " + clock(duration);
        if (duration > 0) seek.value = String(Math.round(1000 * position / duration));
      }
      if (mute.dataset.muted !== (state.muted ? "1" : "0")) {          // its icon: data-muted (booru.css)
        mute.dataset.muted = state.muted ? "1" : "0";
        mute.setAttribute("aria-label", state.muted ? "Включить звук" : "Выключить звук");
      }
      if (document.activeElement !== volume && Date.now() - volumeSet > 1500 && state.volume !== undefined) {
        volume.value = String(state.volume);
      }
      box.classList.toggle("ended", !!state.ended);
      if (state.error) {
        // (v5.44) the program's own player cannot show this file (AV1 without the graphics card's help): the window's
        // browser plays it — once; a page opened for that player already says what went wrong
        if (box.dataset.other && switchPlayer(box.dataset.other)) return;
        showProblem("Видео не открылось: " + state.error);
      }
      renderTracks();
      renderResume(position, duration, playing);
      // the tab «Видео» keeps what was watched and goes on to the next episode (videos.js)
      box.dispatchEvent(new CustomEvent("lb-video-state", { detail: state }));
    }
    // ── «Продолжить просмотр 12:34»: back to the furthest place reached ──
    function renderResume(position, duration, playing) {
      if (!resumeButton) return;
      if (playing && !seeking) {
        if (position >= furthest - 1) resumeUsed = false;       // reached: offered again after a jump back
        furthest = Math.max(furthest, position);
      }
      const ahead = !!token && !resumeUsed && furthest - position > RESUME_GAP &&
        (!duration || furthest < duration - RESUME_END);
      if (ahead) resumeButton.querySelector(".nv-resume-at").textContent = clock(furthest);
      resumeButton.hidden = !ahead;
    }
    function resumeHere() {
      if (!token || !(furthest > 0)) return;
      resumeUsed = true;
      if (resumeButton) resumeButton.hidden = true;
      command("seek", { value: furthest });
    }

    // what the full-size player takes over: the moment, playing or paused, the voice-over and subtitles chosen here
    function moment() {
      const playingNow = token ? !!state.playing : true;
      const at = token ? Number(state.position) || 0 : Number(box.dataset.from) || 0;
      const tracks = {};
      const audio = Array.isArray(state.audio_tracks) ? state.audio_tracks : [];
      if (token && audio.length > 1 && audio[Number(state.audio)]) {
        const t = audio[Number(state.audio)];
        tracks.audio = { name: t.name || "", title: t.title || "", lang: t.lang || "" };
      }
      const subs = Array.isArray(state.subtitles) ? state.subtitles : [];
      if (token && state.subtitle !== undefined) {
        const t = subs.find((s) => s.id === String(state.subtitle));
        if (String(state.subtitle) === "-1") tracks.subtitle = { off: true };
        else if (t) tracks.subtitle = { name: t.name || "", title: t.title || "", lang: t.lang || "", file: !!t.file };
      }
      return { from: at, playing: playingNow, resume: Math.max(furthest, at), tracks };
    }

    // ── voice-overs and subtitles: the file's tracks (the program lists them) and subtitle files next to it ──
    let chosenAt = 0;                            // a menu just used: the state does not move it back meanwhile
    function fill(menu, items, key) {
      if (menu.dataset.key === key) return;
      menu.dataset.key = key;
      menu.textContent = "";
      items.forEach((group) => {
        let parent = menu;
        if (group.label) {
          parent = document.createElement("optgroup");
          parent.label = group.label;
          menu.append(parent);
        }
        group.options.forEach(([value, text]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = text;
          parent.append(option);
        });
      });
    }
    function renderTracks() {
      if (!tracksRow) return;
      const audio = Array.isArray(state.audio_tracks) ? state.audio_tracks : [];
      const subs = Array.isArray(state.subtitles) ? state.subtitles : [];
      fill(audioMenu, [{ options: audio.map((t, n) => [String(n), t.name || "Дорожка " + (n + 1)]) }],
        JSON.stringify(audio));
      const inside = subs.filter((t) => !t.file), beside = subs.filter((t) => t.file);
      const both = inside.length && beside.length;
      fill(subsMenu, [{ options: [["-1", "Без субтитров"]] },
        { label: both ? "В файле видео" : "", options: inside.map((t) => [t.id, t.name]) },
        { label: both ? "Файлы рядом с видео" : "", options: beside.map((t) => [t.id, t.name]) }], JSON.stringify(subs));
      if (Date.now() - chosenAt > 2500) {
        if (document.activeElement !== audioMenu && state.audio !== undefined) audioMenu.value = String(state.audio);
        if (document.activeElement !== subsMenu && state.subtitle !== undefined) subsMenu.value = String(state.subtitle);
      }
      // the voice-overs are chosen under the episodes (videos.js, «.vdubs») — here only at full screen
      const outside = !box.classList.contains("nv-fullscreen") && !!document.querySelector(".vdubs");
      audioMenu.closest(".nv-track").hidden = audio.length < 2 || outside;
      subsMenu.closest(".nv-track").hidden = !subs.length;
      tracksRow.querySelector(".nv-track-note").textContent = state.subtitle_error || "";
      tracksRow.hidden = (audio.length < 2 || outside) && !subs.length && !state.subtitle_error;
    }
    function choose(cmd, value, track) {
      chosenAt = Date.now();
      const t = track || {};
      command(cmd, { value: String(value), name: t.name || "", title: t.title || "", lang: t.lang || "" });
    }
    if (tracksRow) {
      audioMenu.addEventListener("change", () => choose("audio", audioMenu.value,
        (state.audio_tracks || [])[Number(audioMenu.value)]));
      subsMenu.addEventListener("change", () => choose("subtitle", subsMenu.value,
        (state.subtitles || []).find((t) => t.id === subsMenu.value)));
    }
    async function poll() {
      clearTimeout(timer);
      if (!token) return;
      const mine = token;
      try {
        const response = await fetch("/api/video/state?token=" + encodeURIComponent(mine), { credentials: "same-origin" });
        const answer = await response.json();
        if (mine !== token) return;
        if (answer.over) {
          ended();
          return;
        }
        state = answer;
        render();
      } catch (_) {
        /* asked again in a moment */
      }
      timer = setTimeout(poll, 300);
    }
    seek.addEventListener("input", () => {
      seeking = true;
      const duration = Number(state.duration) || 0;
      time.textContent = clock(duration * seek.value / 1000) + " / " + clock(duration);
    });
    seek.addEventListener("change", () => {
      const duration = Number(state.duration) || 0;
      seeking = false;
      if (duration > 0) command("seek", { value: duration * seek.value / 1000 });
    });
    let volumeTimer = 0;
    let volumeSet = 0;                           // when the slider was moved: the state does not move it back meanwhile
    volume.addEventListener("input", () => {
      const value = Number(volume.value);
      volumeSet = Date.now();
      clearTimeout(volumeTimer);
      volumeTimer = setTimeout(() => command("volume", { value }), 60);
    });
    let sizing = 0;
    window.addEventListener("resize", () => {
      clearTimeout(sizing);
      sizing = setTimeout(() => { if (token) command("size", place()); }, 300);
    });
    function stopHere() {
      if (token) appRequest("video", { cmd: "stop", token }).catch(() => {});
      ended();
    }
    // the moment as the program has it now: the last poll may be 0.3 s old — before a jump just made
    async function fresh() {
      if (!token) return;
      const mine = token;
      try {
        const response = await fetch("/api/video/state?token=" + encodeURIComponent(mine), { credentials: "same-origin" });
        const answer = await response.json();
        if (mine === token && !answer.over) state = answer;
      } catch (_) {
        /* the last poll's moment */
      }
    }
    // ── the whole screen: this same player over all of it — the window goes full screen (the program), the page puts
    // the player over itself (.nv-fullscreen); the bar hides while it plays and the mouse rests ──
    let fullOn = false;
    let idleTimer = 0;
    function setFull(on, quiet) {
      on = !!on;
      if (on === fullOn) return;
      fullOn = on;
      box.classList.toggle("nv-fullscreen", on);
      document.documentElement.classList.toggle("lb-video-full", on);
      try {                                      // the next episode (or the same page again) goes on full screen
        if (on) sessionStorage.setItem(FULL_KEY, "1"); else sessionStorage.removeItem(FULL_KEY);
      } catch (_) {
        /* this page only */
      }
      if (fullButton) {
        fullButton.title = on ? "Выйти из полноэкранного режима (Esc, F)" : "Во весь экран (F, двойной щелчок; Esc — выйти)";
        fullButton.setAttribute("aria-label", on ? "Выйти из полноэкранного режима" : "Во весь экран");
        fullButton.setAttribute("aria-pressed", on ? "true" : "false");
      }
      if (inApp && !quiet) appRequest("video-screen", { on }).catch(() => {});
      wake();
      // its frames come at the size they are shown at: the new size once the page has laid it out
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (!on) fitPicture();
        if (token) command("size", place());
      }));
    }
    function wake() {
      box.classList.remove("nv-idle");
      clearTimeout(idleTimer);
      if (!fullOn || !state.playing) return;
      idleTimer = setTimeout(function rest() {
        if (!fullOn || !state.playing) return;
        if (box.querySelector(".nv-bar:hover, .nv-tracks:hover, .nv-resume:hover")) {
          idleTimer = setTimeout(rest, 1500);     // the mouse is on the buttons: they stay
          return;
        }
        box.classList.add("nv-idle");
      }, 2600);
    }
    box.addEventListener("mousemove", wake);
    box.addEventListener("mousedown", wake);
    box.querySelector(".nv-screen").addEventListener("dblclick", (event) => {
      if (event.target.closest("button, a, select, input")) return;
      setFull(!fullOn);
    });

    // the window's own player (mpv where it is installed) — when this one cannot play the file; it goes on from
    // this moment (and gives the moment back: ``resume``)
    async function full() {
      if (box.dataset.stream !== undefined) return setFull(true);   // (v5.44.106) a stream: this player, whole screen
      if (fullOn) setFull(false);              // the window's player shows in the window as it was
      await fresh();
      const what = moment();
      stopHere();
      return appRequest("play-video", Object.assign(box.dataset.episode ? { episode: box.dataset.episode }
        : { id: Number(box.dataset.id) }, what));
    }
    if (frame.complete) fitPicture(); else frame.addEventListener("load", fitPicture, { once: true });
    showSaved();
    if (inApp && carriedFull && box.dataset.full !== "1" && box.dataset.autoplay !== "0") setFull(true);
    // a card's video plays at once, as the old window's did; an episode — when asked (its button, the next one);
    // the next episode after one played full size — full size again
    if (inApp && box.dataset.full === "1") {
      try {
        const u = new URL(location.href);
        u.searchParams.delete("full");                          // a reload of the page stays in the page
        history.replaceState(history.state, "", u.pathname + u.search + u.hash);
      } catch (_) { /* the address stays as it is */ }
      full().catch((err) => showProblem(err.message));
    } else if (inApp && box.dataset.autoplay !== "0") start();
    else if (!inApp) showProblem("Это видео браузер показать не может.");
    return {
      toggle: () => command("toggle"),
      mute: () => command("mute", { value: !state.muted }),
      seekBy: (seconds) => command("seek", { value: Math.max(0, (Number(state.position) || 0) + seconds) }),
      seekTo: (seconds) => command("seek", { value: Math.max(0, Number(seconds) || 0) }),
      audioTracks: () => (token && Array.isArray(state.audio_tracks) ? state.audio_tracks.slice() : []),
      chooseAudio: (n) => choose("audio", n, (state.audio_tracks || [])[n]),
      stop: stopHere,
      inPlayer: full,
      fullscreen: (on) => setFull(on === undefined ? !fullOn : on),
      isFull: () => fullOn,
      // the window left full screen by itself (Esc, F11): the player is back in the page
      screen: (on) => setFull(on, true),
      start,
      restart,
      resumeHere,
      // the full-size player was left at ``position``: this player goes on from there (playing if it played); an
      // episode played to its end — the page goes on (videos.js hears «lb-video-full-left»)
      resume(position, length, playing, over) {
        const at = Math.max(0, Number(position) || 0);
        box.dataset.from = String(over ? 0 : at);
        furthest = Math.max(furthest, at);
        resumeUsed = false;
        box.dispatchEvent(new CustomEvent("lb-video-full-left", {
          detail: { position: at, duration: Number(length) || 0, playing: !!playing, ended: !!over } }));
        if (!over && playing) start();
      },
    };
  })();

  if (inApp && carriedFull && !(nativeVideo && nativeVideo.isFull())) {
    appRequest("video-screen", { on: false }).catch(() => {});   // no player to go on with: the usual window
  }

  document.addEventListener("keydown", (event) => {
    if (!nativeVideo || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (typing(event.target)) return;
    if (event.code === "Space" && !(event.target.closest && event.target.closest("button, a"))) {
      event.preventDefault();
      nativeVideo.toggle();
    } else if (event.code === "KeyJ" || event.code === "KeyL") {
      event.preventDefault();
      nativeVideo.seekBy(event.code === "KeyJ" ? -5 : 5);
    } else if (event.code === "KeyF" && !Object.values(hotkeys).some((k) => String(k).toUpperCase() === "F")) {
      event.preventDefault();                   // (a post page keeps F for «Избранное» — its ⛶ and a double click)
      nativeVideo.fullscreen();
    } else if (event.code === "Escape" && nativeVideo.isFull()) {
      event.preventDefault();                   // (the window's Esc usually comes first: ui/web_gallery_page.py)
      nativeVideo.fullscreen(false);
    } else if (nativeVideo.isFull() && (event.code === "ArrowLeft" || event.code === "ArrowRight")) {
      event.preventDefault();                   // on the whole screen ← → jump as in players (J and L too)
      event.stopImmediatePropagation();
      nativeVideo.seekBy(event.code === "ArrowLeft" ? -5 : 5);
    }
  }, true);

  // ── dragging a picture out of the window (with its tags written into the copy) ──
  document.addEventListener("dragstart", (event) => {
    if (!inApp) return;                       // a browser drags its own way
    const holder = event.target.closest && event.target.closest(".thumb[data-id], .media-wrap[data-id]");
    if (!holder) return;
    event.preventDefault();
    appRequest("drag-file", { id: Number(holder.dataset.id) }).catch((err) => say(null, err.message, true));
  });

  // ── a colour of one's own for a tag: right click on its name ──
  function closeColorBox() {
    const box = document.querySelector(".color-box");
    if (box) box.remove();
  }

  function paintTag(tag, color) {
    document.querySelectorAll('.tag-name[data-tag="' + CSS.escape(tag) + '"]').forEach((a) => {
      a.style.color = color || "";
    });
  }

  async function saveTagColor(tag, color) {
    try {
      await appRequest("tag-color", { tag, color });
      paintTag(tag, color);
    } catch (err) {
      say(null, err.message, true);
    }
  }

  document.addEventListener("contextmenu", (event) => {
    const link = event.target.closest && event.target.closest(".tag-name[data-tag]");
    if (!link) return;
    event.preventDefault();
    closeColorBox();
    const tag = link.dataset.tag;
    const box = document.createElement("div");
    box.className = "color-box";
    box.innerHTML = '<p class="color-title"></p><input type="color" aria-label="Цвет тега">' +
      '<button type="button" class="link" data-reset>Обычный цвет</button>';
    box.querySelector(".color-title").textContent = "Цвет тега «" + tag.replace(/_/g, " ") + "»";
    const input = box.querySelector("input");
    const current = getComputedStyle(link).color.match(/\d+/g) || [0, 0, 0];
    input.value = "#" + current.slice(0, 3).map((n) => Number(n).toString(16).padStart(2, "0")).join("");
    input.addEventListener("change", () => saveTagColor(tag, input.value));
    box.querySelector("[data-reset]").addEventListener("click", () => { saveTagColor(tag, ""); closeColorBox(); });
    document.body.appendChild(box);
    box.style.left = Math.min(event.clientX, window.innerWidth - box.offsetWidth - 8) + "px";
    box.style.top = Math.min(event.clientY, window.innerHeight - box.offsetHeight - 8) + "px";
  });
  document.addEventListener("mousedown", (event) => {
    if (!event.target.closest(".color-box")) closeColorBox();
  });

  // ── the post page keys of the old viewer («Горячие клавиши» in the settings) ──
  const hotkeys = (() => {
    try { return JSON.parse(document.body.dataset.hotkeys || "{}"); } catch (_) { return {}; }
  })();

  function keyName(event) {
    // Letters and digits by their place on the keyboard, so the Russian layout works the same.
    const code = event.code || "";
    if (code.startsWith("Key")) return code.slice(3);
    if (code.startsWith("Digit")) return code.slice(5);
    if (code === "Equal" || code === "NumpadAdd") return "+";
    if (code === "Minus" || code === "NumpadSubtract") return "-";
    if (code === "Numpad0") return "0";
    return event.key.length === 1 ? event.key.toUpperCase() : event.key;
  }

  let zoom = 0;                      // 0 = fitted to the window
  function setZoom(next) {
    const img = postPicture();
    if (!img || !img.naturalWidth) return;
    const wrap = img.closest(".media-wrap");
    if (next <= 0) {
      zoom = 0;
      img.classList.add("fit");
      img.style.width = "";
      if (wrap) wrap.style.maxWidth = "";
      fitPicture();
      return;
    }
    zoom = Math.max(0.1, Math.min(8, next));
    img.classList.remove("fit");
    img.style.width = Math.round(img.naturalWidth * zoom) + "px";
    if (wrap) wrap.style.maxWidth = "none";     // the notes stay over their places when zoomed
    fitPicture();
  }
  function currentZoom() {
    const img = postPicture();
    if (!img || !img.naturalWidth) return 1;
    return zoom || img.clientWidth / img.naturalWidth;
  }

  // (v5.44.40) full size, then bring the clicked part of the picture to where the click was (under the cursor, or
  // the centre when there is no cursor — the button).  Depending on the layout the picture scrolls inside
  // ``.post-stage`` or the whole page scrolls (across as well as down), so both are moved: ``.post-stage`` takes what
  // it can and the page takes the rest.  A big picture then lands on the very part the person pointed at.
  function zoomToPoint(fx, fy, event) {
    const img = postPicture();
    if (!img) return;
    const stage = img.closest(".post-stage");
    setZoom(1);
    if (!img.clientWidth) return;
    const targetX = fx * img.clientWidth;             // the point on the now-full picture
    const targetY = fy * img.clientHeight;
    const anchorX = event ? event.clientX : window.innerWidth / 2;
    const anchorY = event ? event.clientY : window.innerHeight / 2;
    // where that point sits in the window right now (reading the rect flushes the full-size layout first)
    const where = () => { const r = img.getBoundingClientRect(); return [r.left + targetX, r.top + targetY]; };
    if (stage) {                                      // the stage scrolls what it can (clamped on its own)
      const [vx, vy] = where();
      stage.scrollLeft += vx - anchorX;
      stage.scrollTop += vy - anchorY;
    }
    const [vx, vy] = where();                         // whatever the stage could not scroll, the page does
    window.scrollBy(vx - anchorX, vy - anchorY);
  }

  const hotkeyActions = {
    previous: () => { const a = document.querySelector('a[data-key="ArrowLeft"]'); if (a) location.href = a.href; },
    next: () => { const a = document.querySelector('a[data-key="ArrowRight"]'); if (a) location.href = a.href; },
    favorite: () => { const b = document.querySelector('[data-action="favorite"]'); if (b) b.click(); },
    fit: () => { const img = postPicture(); if (img) { setZoom(img.classList.contains("fit") ? 1 : 0); } },
    volume: () => {
      const v = document.querySelector("video.post-media");
      if (v) v.muted = !v.muted;
      else if (nativeVideo) nativeVideo.mute();
    },
    back: () => { const a = document.querySelector(".nav-back"); if (a) location.href = a.href; },
    fullscreen: () => {
      if (inApp) appRequest("fullscreen").catch(() => {});
      else if (document.fullscreenElement) document.exitFullscreen();
      else document.documentElement.requestFullscreen();
    },
    zoom_in: () => setZoom(currentZoom() * 1.25),
    zoom_out: () => setZoom(currentZoom() / 1.25),
    zoom_reset: () => setZoom(0),
    notes: () => { const b = document.querySelector('[data-action="notes"]'); if (b) b.click(); },
  };

  if (Object.keys(hotkeys).length) {
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || typing(event.target)) return;
      const name = keyName(event);
      for (const [action, combo] of Object.entries(hotkeys)) {
        if (String(combo).toUpperCase() === name.toUpperCase() && hotkeyActions[action]) {
          if (action === "fullscreen" && !inApp && name === "F11") return;   // the browser's own F11
          event.preventDefault();
          hotkeyActions[action]();
          return;
        }
      }
    });
  }

  // A video the window cannot play: offer the usual player instead of an empty frame.  (v5.44) In the program window
  // the page first goes over to the program's own player (data-other — this page with it): the window's browser has
  // no H.264, H.265 or AAC, and a «.webm» may have them inside; a picture that never shows (H.265: the sound plays,
  // the picture stays empty) is the same failure.
  document.querySelectorAll("video.post-media, video.vvideo").forEach((video) => {
    const fallback = (video.closest(".vstage") || video.parentElement).querySelector(".video-fallback");
    const giveUp = () => {
      if (video.dataset.other && switchPlayer(video.dataset.other)) return;
      if (!fallback || !video.classList.contains("post-media")) return;   // the tab «Видео»: videos.js says it
      video.hidden = true;
      fallback.hidden = false;
    };
    video.addEventListener("error", giveUp);
    if (video.error) giveUp();             // failed before this script ran
    if (video.dataset.picture === "1") {
      const noPicture = () => { if (video.readyState >= 2 && !video.videoWidth) giveUp(); };
      video.addEventListener("loadeddata", noPicture);
      noPicture();
    }
  });

  // tools.js (the parser page, many cards at once) adds its buttons to the same dispatcher.
  window.LocalBooru = { actions, postJson, say, appRequest, inApp, nativeVideo, copyText };
})();
