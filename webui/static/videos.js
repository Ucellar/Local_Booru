// The tab «Видео» (webui/pages/videos.py): what was watched and the next episode, the description of a series
// from a site, the folder buttons.  The video itself: in the program window the program plays it (booru.js, the
// box .native-video[data-episode] says its state with the event «lb-video-state»); in a browser — <video>.
(() => {
  "use strict";
  const LB = window.LocalBooru;
  if (!LB) return;
  const { actions, postJson, say } = LB;

  // ── buttons ──────────────────────────────────────────────────────────────
  actions["vid-make-folder"] = async (el) => {
    try {
      const reply = await postJson("/api/videos/folder", {});
      say(el, "Папка создана: " + reply.path);
      setTimeout(() => location.reload(), 900);
    } catch (err) {
      say(el, err.message, true);
    }
  };

  actions["vid-choose-folder"] = async (el) => {
    if (!LB.inApp) {
      location.href = "/settings#set-folders";
      return;
    }
    try {
      await LB.appRequest("choose-video-folder");
    } catch (err) {
      say(el, err.message, true);
    }
  };

  const post = (address, el, done) => postJson(address, {}).then(() => { if (done) say(el, done); })
    .catch((err) => say(el, err.message, true));
  actions["vid-reveal-root"] = (el) => post("/api/videos/root/reveal", el);
  actions["vid-reveal"] = (el) => post("/api/videos/" + el.dataset.id + "/reveal", el);
  actions["vid-open"] = (el) => {
    const box = document.querySelector(".native-video[data-episode]");
    if (box && LB.nativeVideo) LB.nativeVideo.stop();        // the file goes to the other player alone
    const video = document.querySelector("video.vvideo");
    if (video) video.pause();
    return post("/api/videos/episode/" + el.dataset.id + "/open", el, "Открыто в видеоплеере");
  };

  // ── the person's mark of a series, 1…10: the same star again takes it back ──
  function showMark(box, mark) {
    box.querySelectorAll(".vstar-btn").forEach((star) => {
      const n = Number(star.dataset.value);
      star.classList.toggle("on", n <= mark);
      star.setAttribute("aria-checked", n === mark ? "true" : "false");
      star.title = n + " из 10" + (n === mark ? " — нажмите ещё раз, чтобы убрать" : "");
    });
    box.querySelector(".vrate-value").textContent = mark ? mark + " из 10" : "не оценено";
    box.dataset.mark = String(mark);
  }

  actions["vid-rate"] = async (el) => {
    const box = el.closest(".vrate");
    const before = Number(box.dataset.mark || box.querySelectorAll(".vstar-btn.on").length);
    const wanted = Number(el.dataset.value);
    const mark = wanted === before ? 0 : wanted;
    showMark(box, mark);
    try {
      await postJson("/api/videos/" + box.dataset.series + "/rating", { value: mark });
    } catch (err) {
      showMark(box, before);
      say(el, err.message, true);
    }
  };

  actions["vid-desc"] = (el) => {
    const box = el.closest("[data-desc]");
    const open = box.classList.toggle("open");
    el.textContent = open ? "Свернуть" : "Показать полностью";
  };

  actions["vid-group"] = (el) => {
    const wanted = el.dataset.group;
    document.querySelectorAll(".vgroups [data-group]").forEach((tab) => {
      tab.setAttribute("aria-selected", tab === el ? "true" : "false");
    });
    document.querySelectorAll(".vep-list[data-group]").forEach((list) => {
      list.hidden = list.dataset.group !== wanted;
    });
  };

  actions["vid-meta-open"] = () => {
    const box = document.getElementById("vid-meta");
    if (!box) return;
    box.hidden = !box.hidden;
    if (!box.hidden) {
      box.scrollIntoView({ block: "nearest", behavior: "smooth" });
      const field = box.querySelector('input[name="url"]');
      if (field) field.focus();
    }
  };

  // ── what was watched; the next episode ─────────────────────────────────────
  const page = document.querySelector(".vseries");
  const nativeBox = document.querySelector(".native-video[data-episode]");
  const htmlVideo = document.querySelector("video.vvideo[data-episode]");
  const holder = nativeBox || htmlVideo;
  const autoBox = document.querySelector("input[data-autonext]");

  if (autoBox) {
    autoBox.addEventListener("change", () => {
      const on = autoBox.checked ? "on" : "off";
      document.body.dataset.autonext = on;
      postJson("/api/prefs", { video_autonext: on }).catch((err) => say(autoBox, err.message, true));
    });
  }

  if (page && holder) {
    const episode = holder.dataset.episode;
    const next = holder.dataset.next || "";
    let last = { position: 0, duration: 0 };
    let sentAt = 0;
    let sentPosition = -1;
    let finished = false;

    const send = (ended, keepalive) => {
      if (!last.duration && !ended) return;
      if (!ended && Math.abs(last.position - sentPosition) < 1) return;
      sentPosition = last.position;
      sentAt = Date.now();
      const body = JSON.stringify({ episode, position: last.position, duration: last.duration, ended: !!ended });
      fetch("/api/videos/progress", {
        method: "POST", credentials: "same-origin", keepalive: !!keepalive,
        headers: { "Content-Type": "application/json" }, body,
      }).catch(() => {});
    };

    const goOn = (fullSize) => {
      if (finished) return;
      finished = true;
      send(true, true);
      const on = !autoBox || autoBox.checked;
      if (next && on && document.body.dataset.autonext !== "off") {
        let target = next;
        if (fullSize) {                         // the next one full size too, as this one was watched
          try {
            const u = new URL(next, location.href);
            u.searchParams.set("full", "1");
            target = u.href;
          } catch (_) { /* the plain address */ }
        }
        setTimeout(() => { location.href = target; }, fullSize ? 0 : 800);
      }
    };

    const seen = (position, duration, playing) => {
      last = { position: Number(position) || 0, duration: Number(duration) || 0 };
      if (playing && Date.now() - sentAt > 10000) send(false, false);
    };

    if (nativeBox) {
      // the full-size player was left: it kept what was watched itself (the program); its moment is this page's now
      nativeBox.addEventListener("lb-video-full-left", (event) => {
        const d = event.detail || {};
        last = { position: Number(d.position) || 0, duration: Number(d.duration) || 0 };
        sentPosition = last.position;
        if (d.ended) goOn(true);
      });
      let wasPlaying = false;
      nativeBox.addEventListener("lb-video-state", (event) => {
        const st = event.detail || {};
        seen(st.position, st.duration, st.playing);
        if (wasPlaying && !st.playing && !st.ended) send(false, false);       // paused: kept at once
        wasPlaying = !!st.playing;
        if (st.ended) goOn();
      });
    } else {
      const from = Number(htmlVideo.dataset.from) || 0;
      if (from > 0) {
        const jump = () => { if (htmlVideo.duration && from < htmlVideo.duration - 1) htmlVideo.currentTime = from; };
        if (htmlVideo.readyState >= 1) jump(); else htmlVideo.addEventListener("loadedmetadata", jump, { once: true });
      }
      htmlVideo.addEventListener("timeupdate", () => seen(htmlVideo.currentTime, htmlVideo.duration, !htmlVideo.paused));
      htmlVideo.addEventListener("pause", () => { if (!htmlVideo.ended) send(false, false); });
      htmlVideo.addEventListener("ended", goOn);
      const fallback = (htmlVideo.closest(".vstage") || htmlVideo.parentElement).querySelector(".video-fallback");
      const giveUp = () => {
        htmlVideo.hidden = true;
        const screen = htmlVideo.closest(".vscreen");          // with the frames over it
        if (screen) screen.hidden = true;
        if (fallback) fallback.hidden = false;
      };
      htmlVideo.addEventListener("error", giveUp);
      if (htmlVideo.error) giveUp();
      // the subtitles chosen in the browser's menu are kept for the series: its next episode opens with them
      const shownTrack = () => {
        const tracks = Array.from(htmlVideo.textTracks || []);
        const n = tracks.findIndex((t) => t.mode === "showing");
        return n < 0 ? "-1" : "x" + n;
      };
      if (htmlVideo.textTracks && htmlVideo.textTracks.length) {
        let shown = shownTrack();
        htmlVideo.textTracks.addEventListener("change", () => {
          const now = shownTrack();
          if (now === shown) return;
          shown = now;
          postJson("/api/videos/tracks", { episode, value: now }).catch(() => {});
        });
      }
    }
    window.addEventListener("pagehide", () => send(false, true));
    document.addEventListener("visibilitychange", () => { if (document.hidden) send(false, true); });
    setupSkips(holder);
  }

  // ── «Пропустить опенинг» (on the left) / «Пропустить эндинг» (on the right): shown only while it plays; their
  // stretches marked on the bar in the look's colour.  A series' episode only (the page gives data-skips then).
  // ── «Озвучка» under the episodes: the voice-overs of the episode's file; the program's player switches at once,
  // before the play the choice is kept for the series (its play starts with it) ──
  function setupDubs() {
    const row = document.querySelector(".vdubs");
    if (!row) return;
    const now = row.querySelector(".vdubs-now");
    const name = row.querySelector(".vdubs-name");
    const list = row.querySelector(".vdubs-list");
    const options = [...list.querySelectorAll(".vdub")];
    const mark = (title) => {
      for (const o of options) o.setAttribute("aria-selected", o.dataset.title === title ? "true" : "false");
      name.textContent = title;
    };
    const open = (on) => {
      list.hidden = !on;
      now.setAttribute("aria-expanded", on ? "true" : "false");
      if (on) (options.find((o) => o.getAttribute("aria-selected") === "true") || options[0]).focus();
    };
    now.addEventListener("click", (event) => {
      event.preventDefault();
      open(list.hidden);
    });
    document.addEventListener("click", (event) => { if (!list.hidden && !row.contains(event.target)) open(false); });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !list.hidden) {
        open(false);
        now.focus();
      } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !list.hidden) {
        event.preventDefault();
        const at = options.indexOf(document.activeElement);
        const next = options[(at + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length];
        if (next) next.focus();
      }
    });
    list.addEventListener("click", (event) => {
      const option = event.target.closest(".vdub");
      if (!option) return;
      const title = option.dataset.title;
      mark(title);
      open(false);
      now.focus();
      const player = window.LocalBooru && window.LocalBooru.nativeVideo;
      const tracks = player && player.audioTracks ? player.audioTracks() : [];
      const n = tracks.findIndex((t) => String(t.title || "").trim() === title);
      if (n >= 0) player.chooseAudio(n);                     // playing: switched now (the program keeps it)
      else postJson("/api/videos/tracks", { episode: row.dataset.episode, audio: title }).catch(() => {});
    });
    const box = document.querySelector(".native-video[data-episode]");
    if (box) {
      box.addEventListener("lb-video-state", (event) => {
        const st = event.detail || {};
        const track = (st.audio_tracks || [])[Number(st.audio)];
        if (track && list.hidden) {
          const title = String(track.title || "").trim();
          if (title && options.some((o) => o.dataset.title === title)) mark(title);
        }
      });
    }
  }
  setupDubs();

  // ── «Порядок просмотра» on a series' page (a title from YummyAnime): its seasons, films and extras — the ones in
  // the library open there, the others are found and downloaded on «Скачать с сайта» (/api/videos/order/<id>) ──
  function setupOrder(box) {
    fetch("/api/videos/order/" + encodeURIComponent(box.dataset.order), { credentials: "same-origin" })
      .then((r) => r.json()).then((reply) => {
        showRatings((reply && reply.ratings) || []);
        const order = (reply && reply.order) || [];
        if (order.length < 2) return;
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "vbutton primary vorder-toggle";
        toggle.setAttribute("aria-expanded", "false");
        toggle.innerHTML = '<span>Порядок просмотра</span><span class="vorder-arrow" aria-hidden="true">▾</span>';
        const list = document.createElement("ol");
        list.className = "vorder-list";
        list.hidden = true;
        for (const o of order) {
          const li = document.createElement("li");
          li.className = o.here ? "here" : o.series ? "have" : o.soon ? "soon" : "";
          let name;
          if (o.series && !o.here) {
            name = document.createElement("a");
            name.href = "/videos/s/" + encodeURIComponent(o.series);
          } else {
            name = document.createElement(o.here ? "b" : "span");
          }
          name.className = "vorder-name";
          name.textContent = o.title;
          li.append(name);
          const about = [o.type, o.kind, o.year].filter(Boolean).join(", ");
          if (about) li.append(document.createTextNode(" — " + about));
          const mark = document.createElement("span");
          mark.className = "vorder-mark";
          if (o.here) mark.textContent = "это";
          else if (o.series) mark.textContent = "есть" + (o.episodes ? ": " + o.episodes + " " + (o.episodes % 10 === 1 && o.episodes % 100 !== 11 ? "серия" : [2, 3, 4].includes(o.episodes % 10) && ![12, 13, 14].includes(o.episodes % 100) ? "серии" : "серий") : "");
          else if (o.soon) mark.textContent = "анонс";
          else if (o.get) {
            const get = document.createElement("a");
            get.href = o.get;
            get.textContent = "найти и скачать";
            mark.append(get);
          }
          li.append(mark);
          list.append(li);
        }
        toggle.addEventListener("click", () => {
          list.hidden = !list.hidden;
          toggle.setAttribute("aria-expanded", String(!list.hidden));
        });
        box.append(toggle, list);
        box.hidden = false;
      }).catch(() => {});
  }
  document.querySelectorAll(".vorder[data-order]").forEach(setupOrder);

  // the title's ratings as YummyAnime has them now (its own people's first): «Оценки» of the series written then
  function showRatings(list) {
    const facts = document.querySelector(".vhero .vfacts");
    if (!facts || !list.length) return;
    let dd = null;
    for (const dt of facts.querySelectorAll("dt")) if (dt.textContent.trim() === "Оценки") dd = dt.nextElementSibling;
    if (!dd) {
      const dt = document.createElement("dt");
      dt.textContent = "Оценки";
      dd = document.createElement("dd");
      const files = [...facts.querySelectorAll("dt")].find((x) => x.textContent.trim() === "Файлов");
      if (files) { facts.insertBefore(dt, files); facts.insertBefore(dd, files); } else facts.append(dt, dd);
    }
    dd.textContent = "";
    list.forEach(([name, value], n) => {
      if (n) dd.append(" · ");
      dd.append(name + " ");
      const b = document.createElement("b");
      b.textContent = value;
      dd.append(b);
    });
  }

  function setupSkips(holder) {
    let skips = {};
    try { skips = JSON.parse(holder.dataset.skips || "{}") || {}; } catch (_) { skips = {}; }
    const kinds = ["opening", "ending"].filter((k) => Array.isArray(skips[k]) && skips[k][1] > skips[k][0]);
    if (!kinds.length) return;
    const screen = holder.matches("video") ? holder.closest(".vscreen") : holder.querySelector(".nv-screen");
    const buttons = {};
    for (const kind of kinds) {
      const button = screen && screen.querySelector('.nv-skip[data-skip="' + kind + '"]');
      if (!button) continue;
      buttons[kind] = button;
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const to = skips[kind][1];
        if (holder.matches("video")) holder.currentTime = to;
        else if (window.LocalBooru && window.LocalBooru.nativeVideo) window.LocalBooru.nativeVideo.seekTo(to);
        button.hidden = true;
      });
    }
    // the bar: a thin stripe under the track of the program's player; under the browser's player a strip of its own
    let marked = 0;
    const mark = (duration) => {
      if (!(duration > 0) || Math.abs(duration - marked) < 0.5) return;
      marked = duration;
      const stops = [];
      for (const kind of kinds) {
        const a = Math.max(0, Math.min(100, 100 * skips[kind][0] / duration));
        const b = Math.max(0, Math.min(100, 100 * skips[kind][1] / duration));
        stops.push("transparent " + a.toFixed(2) + "%", "var(--accent, #4a90d9) " + a.toFixed(2) + "%",
          "var(--accent, #4a90d9) " + b.toFixed(2) + "%", "transparent " + b.toFixed(2) + "%");
      }
      const image = "linear-gradient(to right, transparent 0%, " + stops.join(", ") + ", transparent 100%)";
      if (holder.matches("video")) {
        let strip = screen && screen.parentElement.querySelector(".vskips");
        if (!strip && screen) {
          strip = document.createElement("div");
          strip.className = "vskips";
          strip.setAttribute("aria-hidden", "true");
          screen.insertAdjacentElement("afterend", strip);
        }
        if (strip) strip.style.backgroundImage = image;
      } else {
        // a range's own look paints no background: a stripe of its own under it, in a wrapper the size of the range
        const seek = holder.querySelector(".nv-seek");
        let marks = holder.querySelector(".nv-marks");
        if (seek && !marks) {
          const wrap = document.createElement("span");
          wrap.className = "nv-seekwrap";
          seek.parentNode.insertBefore(wrap, seek);
          wrap.appendChild(seek);
          marks = document.createElement("span");
          marks.className = "nv-marks";
          marks.setAttribute("aria-hidden", "true");
          wrap.appendChild(marks);
        }
        if (marks) marks.style.backgroundImage = image;
      }
    };
    const show = (position, duration) => {
      mark(duration);
      for (const kind of Object.keys(buttons)) {
        const [a, b] = skips[kind];
        buttons[kind].hidden = !(position >= a && position < b - 1);
      }
    };
    if (holder.matches("video")) {
      holder.addEventListener("timeupdate", () => show(holder.currentTime, holder.duration));
      holder.addEventListener("loadedmetadata", () => mark(holder.duration));
      mark(holder.duration || Number(skips.length) || 0);
    } else {
      holder.addEventListener("lb-video-state", (event) => {
        const st = event.detail || {};
        show(Number(st.position) || 0, Number(st.duration) || Number(holder.dataset.length) || Number(skips.length) || 0);
      });
      mark(Number(holder.dataset.length) || Number(skips.length) || 0);
    }
  }

  // ── the chosen episode, not played yet: its frames one after another, each fading into the next ──
  // The program makes them (/videos/frame/<episode>?n=0…count-1) one at a time, a little before each is shown; a
  // frame it cannot make is passed over.  They stop while the episode plays: the program's player marks its box
  // «started» (booru.js), a browser's <video> plays under them — then they are gone for good.
  const slides = document.querySelector(".vslides[data-episode]");
  const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (slides && !still) {
    const FIRST = 1500;                    // ms: the poster alone at first
    const SHOW = 5500;                     // ms each frame stays
    const FADE = 1400;                     // ms of the fade (videos.css .vslide)
    const count = Number(slides.dataset.count) || 0;
    const box = slides.closest(".native-video");
    const video = slides.closest(".vscreen") ? slides.closest(".vscreen").querySelector("video.vvideo") : null;
    const button = slides.querySelector(".nv-big");
    const pictures = [];                   // the frames got, in their order
    let asked = 0;                         // the next frame to ask for
    let at = -1;                           // the one on view
    let coming = null;                     // the next frame, being got
    let timer = 0;

    const gone = () => slides.classList.contains("gone");
    const watching = () => gone() || (box && box.classList.contains("started")) || (video && !video.paused);
    const fetchNext = () => new Promise((resolve) => {
      if (asked >= count) { resolve(null); return; }
      const img = new Image();
      img.className = "vslide";
      img.alt = "";
      img.decoding = "async";
      img.onload = () => resolve(img);
      img.onerror = () => resolve(fetchNext());              // no frame there: the next one
      img.src = "/videos/frame/" + encodeURIComponent(slides.dataset.episode) + "?n=" + asked++;
    });
    const show = (img) => {
      const before = slides.querySelector(".vslide.on");
      if (img === before) return;
      slides.insertBefore(img, button);                       // over the others, under the button
      img.classList.remove("on");
      void img.offsetWidth;                                   // transparent first, then it fades in
      img.classList.add("on");
      if (before) setTimeout(() => before.classList.remove("on"), FADE + 50);
    };
    const tick = async () => {
      timer = 0;
      if (gone()) return;
      if (!watching() && !document.hidden) {
        if (!coming && asked < count) coming = fetchNext();
        const fresh = coming ? await coming : null;
        coming = null;
        if (fresh) {
          pictures.push(fresh);
          at = pictures.length - 1;
        } else if (pictures.length > 1) {
          at = (at + 1) % pictures.length;
        }
        if (gone()) return;
        if (at >= 0 && !watching()) show(pictures[at]);
        if (asked < count) coming = fetchNext();             // ready by its turn
      }
      timer = setTimeout(tick, SHOW);
    };
    if (video) {
      video.addEventListener("play", () => {
        slides.classList.add("gone");
        clearTimeout(timer);
      });
    }
    timer = setTimeout(tick, FIRST);
  }
  actions["vid-play"] = () => {
    const video = document.querySelector("video.vvideo");
    if (!video) return;
    const started = video.play();
    if (started && started.catch) started.catch(() => {});
  };

  // ── «Описание с сайта» ───────────────────────────────────────────────────
  const meta = document.getElementById("vid-meta");
  if (meta) {
    const series = meta.dataset.series;
    const job = meta.querySelector("[data-job]");
    const stateUrl = "/api/videos/meta-state?series=" + encodeURIComponent(series);
    const buttons = () => meta.querySelectorAll('button[type="submit"]');
    const replace = () => (meta.querySelector("[data-meta-replace]") || {}).checked;
    let watcher = null;

    const watch = () => {
      watcher = watcher || LB.watchTask(job, stateUrl, {
        onState: (st, busy) => buttons().forEach((b) => { b.disabled = busy; }),
        onEnd(st) {
          if (st.status === "done") setTimeout(() => location.reload(), 1200);
        },
      });
      return watcher;
    };

    const started = (watching) => {
      meta.hidden = false;
      job.hidden = false;
      if (typeof watching === "function") watching();
      else if (watching && watching.started) watching.started();
    };

    meta.querySelector('[data-form="vid-meta-url"]').addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      try {
        await postJson("/api/videos/" + series + "/meta", { url: form.url.value.trim(), replace_poster: replace() });
        say(button, "Беру описание…");
        started(watch());
      } catch (err) {
        say(button, err.message, true);
      }
    });

    meta.querySelector('[data-form="vid-meta-file"]').addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      const file = form.file.files && form.file.files[0];
      if (!file) {
        say(button, "Выберите файл сохранённой страницы", true);
        return;
      }
      button.disabled = true;
      say(button, "Отправляю файл…");
      try {
        const address = "/api/videos/" + series + "/meta-file?name=" + encodeURIComponent(file.name) +
          (replace() ? "&replace=1" : "");
        const response = await fetch(address, {
          method: "POST", credentials: "same-origin", body: file,
          headers: { "Content-Type": "application/octet-stream" },
        });
        const reply = await response.json().catch(() => ({ ok: false, error: "Ответ " + response.status }));
        if (!response.ok || !reply.ok) throw new Error(reply.error || "Ответ " + response.status);
        say(button, "Читаю страницу…");
        started(watch());
      } catch (err) {
        say(button, err.message, true);
      } finally {
        button.disabled = false;
      }
    });

    fetch(stateUrl, { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => { if (st.id && st.status && st.status !== "idle") { meta.hidden = false; watch(); } })
      .catch(() => {});
  }

  // ── «Скачать с сайта» (/videos/get): find the files of a page, download the chosen ones ─────────────
  const getBox = document.getElementById("vid-get");
  if (getBox) setupGet(getBox);

  function clock(seconds) {
    const s = Math.round(Number(seconds) || 0);
    const two = (n) => String(n).padStart(2, "0");
    return s >= 3600 ? `${Math.floor(s / 3600)}:${two(Math.floor(s % 3600 / 60))}:${two(s % 60)}`
      : `${Math.floor(s / 60)}:${two(s % 60)}`;
  }

  function megabytes(bytes) {
    const n = Number(bytes) || 0;
    if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(2).replace(".", ",") + " ГБ";
    if (n >= 1024 ** 2) return Math.round(n / 1024 ** 2) + " МБ";
    return Math.round(n / 1024) + " КБ";
  }

  // «1 серия», «2 серии», «5 серий» (forms: for 1, for 2–4, for 5–20)
  function plural(n, forms) {
    const d = n % 10, h = n % 100;
    return n + " " + (d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2]);
  }

  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  // the state of a file on its tile: what is in the folder, why it cannot be had, how its download goes
  const HAVE_MARKS = { whole: "есть", part: "докачается", other: "свой файл" };
  const RUN_MARKS = { queued: "в очереди", active: "качается", paused: "пауза", done: "скачана", whole: "уже была",
    other: "свой файл", failed: "не скачалась", dead: "пустая на сайте", stopped: "остановлена" };
  const RUNNING = ["starting", "running", "stopping"];

  function setupGet(box) {
    const findJob = box.querySelector('[data-job="find"]');
    const step = findJob.querySelector(".vget-step");
    const foundBox = box.querySelector(".vfound");
    const list = foundBox.querySelector(".vfound-list");
    const pickCount = foundBox.querySelector(".vpick-count");
    const goButton = foundBox.querySelector('[data-action="vid-download"]');
    const sum = foundBox.querySelector(".vbar-sum");
    const getJob = foundBox.querySelector('[data-job="download"]');
    const stopButton = getJob.querySelector('[data-action="vid-download-stop"]');
    const pauseButton = getJob.querySelector('[data-action="vid-download-pause"]');
    const meterBox = getJob.querySelector(".vmeter");
    const result = getJob.querySelector(".vget-result");
    const openLink = getJob.querySelector(".vget-open");
    const queueBox = getJob.querySelector(".vqueue");
    const fileInput = box.querySelector("#vid-find-file");
    const qualityBox = foundBox.querySelector(".vquality");
    const qualityPick = qualityBox.querySelector("select");
    const mergeBox = foundBox.querySelector(".vmerge");
    const mergeTick = mergeBox.querySelector("input");
    let findId = 0;
    let found = null;                // the list shown (the search's result)
    let lastLine = "";               // the search's newest line: what it does now
    let lastPicked = -1;             // shift+click: the tiles from this one to the clicked one
    let runStates = {};              // file number -> how its download went (the download of this list)
    let runActive = { file: -1, part: 0 };
    let runMine = false;             // the download going (or last) is of this list
    let queueHere = null;            // this list's place in the queue of downloads (another one goes first)
    let findWatch = null;
    let getWatch = null;

    const findButtons = () => box.querySelectorAll('.vget-ask button[type="submit"], .vget-ask input[type="file"]');
    const downloading = () => RUNNING.includes(getJob.dataset.status || "");
    const busyHere = () => downloading() && runMine;            // the download going is of this list
    const keepQueued = () => busyHere() || !!queueHere;          // «в очереди» stays: its turn will come
    const tiles = () => [...list.querySelectorAll(".vtile")];
    const manyDubs = () => !!found && found.choose === "dubs";   // every voice-over of a title: nothing ticked at first
    // every voice-over of a title: the voice-overs and the subtitles' releases chosen in their plates, the episodes as
    // tiles — what is downloaded is each episode ticked of each release ticked
    const dubs = { groups: new Set(), episodes: new Set(), measured: null, measuring: 0, bar: null };
    const isSubs = (name) => /^субтитры/i.test(name || "");
    const natural = (a, b) => (parseFloat(a) - parseFloat(b)) || String(a).localeCompare(String(b), "ru");

    // ── the search: one line of what it does, its journal under «Подробности» ──
    function showStep(st, busy) {
      const lines = st.log || [];
      if (lines.length) lastLine = lines[lines.length - 1].text;
      step.classList.toggle("error", st.status === "failed");
      if (busy) step.textContent = lastLine || "Читаю страницу…";
      else if (st.status === "failed") step.textContent = st.error || "Не вышло — причина в подробностях";
      else if (st.status === "stopped") step.textContent = "Поиск остановлен";
      else step.textContent = "";
      step.hidden = !step.textContent;
    }

    // ── the title's card ──
    function showCard(st) {
      const facts = found.facts || {};
      const sources = found.sources || [];
      foundBox.querySelector(".vfound-head").textContent = sources.length
        ? "Нашлось на " + sources.map((x) => x.site).join(", ") : "Нашлось на странице";
      foundBox.querySelector(".vfound-title").textContent = found.title || "Без названия";
      const alt = foundBox.querySelector(".vfound-alt");
      alt.textContent = (facts.alt || []).join(" / ");
      alt.hidden = !alt.textContent;
      const factList = foundBox.querySelector(".vfound-facts");
      factList.textContent = "";
      const episodes = String(facts.episodes || "").trim();
      for (const text of [facts.year, facts.type, episodes ? "серий: " + episodes : ""]) {
        if (text) factList.append(element("li", "", text));
      }
      for (const genre of facts.genres || []) factList.append(element("li", "genre", genre));
      factList.hidden = !factList.children.length;
      const poster = foundBox.querySelector(".vcard-poster img");
      const initial = foundBox.querySelector(".vcard-initial");
      initial.textContent = (found.title || "?").trim().charAt(0).toUpperCase();
      poster.hidden = true;
      poster.removeAttribute("src");
      if (found.poster) {
        poster.onload = () => { poster.hidden = false; };
        poster.onerror = () => { poster.hidden = true; };
        poster.src = "/api/videos/find-poster?find=" + encodeURIComponent(st.id);
      }
      // the episodes of another site: which release, how it was matched, which voice-overs could be had
      const sourceBox = foundBox.querySelector(".vfound-sources");
      sourceBox.textContent = "";
      for (const x of sources) {
        const line = element("p", "vfound-source" + (x.exact ? "" : " warn"));
        line.append(element("b", "", (x.dub || "Серии — с " + x.site) + ": "), x.what + " — " + x.how + ". "
          + (x.about === "page" ? "Описание, жанры и постер — со страницы, которую вы вставили."
            : "Описание и постер — тоже с " + x.site + ".")
          + (x.ongoing ? " Тайтл ещё выходит: новые серии докачаются, если потом снова найти и скачать." : ""));
        sourceBox.append(line);
        // a related title's episodes the site hung on this page (a recap with the whole season): not offered here
        for (const other of x.borrowed || []) {
          const warn = element("p", "vfound-source warn vfound-borrowed");
          warn.append(element("b", "", "Не этого тайтла: "), x.site + " приложил к этой странице видео тайтла «"
            + other.title + "» (" + other.videos + " шт.) — их здесь нет, чтобы под этим именем не скачались чужие серии. ");
          if (other.address) {
            const go = element("button", "vfound-borrowed-go", "Искать серии «" + other.title + "»");
            go.type = "button";
            go.dataset.address = other.address;
            go.addEventListener("click", () => findOther(other.address));
            warn.append(go);
          }
          sourceBox.append(warn);
        }
        // the site's «Порядок просмотра»: the franchise's seasons, films and extras; a click searches that one
        const order = x.order || [];
        if (order.length > 1) {
          const block = element("div", "vfound-order");
          block.append(element("span", "vfound-order-label", "Порядок просмотра на " + x.site + ":"));
          const items = element("ol", "vfound-order-list");
          for (const o of order) {
            const li = element("li", o.here ? "here" : o.soon ? "soon" : "");
            const about = [o.type, o.year, o.kind, o.soon ? "анонс" : ""].filter(Boolean).join(" · ");
            if (o.here || o.soon || !o.address) {
              li.append(element("span", "vfound-order-name", o.title + (o.here ? " — эта страница" : "")));
            } else {
              const go = element("button", "vfound-order-go", o.title);
              go.type = "button";
              go.title = "Найти серии: " + o.address;
              go.addEventListener("click", () => findOther(o.address));
              li.append(go);
            }
            if (about) li.append(element("span", "vfound-order-about", about));
            items.append(li);
          }
          block.append(items);
          sourceBox.append(block);
        }
      }
      const voices = found.voices || [];
      if (manyDubs() && voices.length) {
        const line = element("p", "vfound-voices");
        line.append(element("span", "vfound-voices-label", "Только в плеерах, из которых программа не скачивает:"));
        for (const v of voices) line.append(element("span", "voice", v.name + (v.players && v.players.length ? " (" + v.players.join(", ") + ")" : "")));
        sourceBox.append(line);
      } else if (sources.length && voices.length) {
        const line = element("p", "vfound-voices");
        line.append(element("span", "vfound-voices-label", "Озвучки на странице:"));
        for (const v of voices) {
          const chip = element("span", "voice" + (v.here ? " here" : ""), v.name + (v.here ? " — скачать можно" : ""));
          line.append(chip);
        }
        if (voices.some((v) => !v.here)) line.append(element("span", "vfound-voices-rest", "остальные — только в закрытом плеере страницы"));
        sourceBox.append(line);
      }
      sourceBox.hidden = !sourceBox.children.length;
      const folderName = String(found.folder || "").split(/[\\/]/).filter(Boolean).pop() || found.folder || "";
      const where = foundBox.querySelector(".vfound-where");
      where.textContent = "Папка «" + folderName + "» — " + (found.new_folder ? "новая" : "уже есть, новые серии лягут в неё");
      where.title = String(found.folder || "");
      const notes = [];
      if (found.episodes) notes.push("Серии сайт показывает на своих страницах — прочитано страниц серий: " + found.episodes + ".");
      if (found.streams) notes.push("Ещё на странице видео потоком DASH (.mpd): такие программа не скачивает.");
      if (found.hidden) notes.push("Часть ссылок плеер прячет (зашифрованы или вместо них заглушки): такие программа не скачивает.");
      if (found.frames && found.frames.length) notes.push("На странице есть плеер другого сайта (" + found.frames.join(", ") + "): оттуда программа не скачивает.");
      const notesBox = foundBox.querySelector(".vfound-notes");
      notesBox.textContent = "";
      notes.forEach((text) => notesBox.append(element("p", "", text)));
    }

    // ── the episodes as tiles, by their voice-overs ──
    function tile(f) {
      const label = element("label", "vtile" + (f.kind === "audio" ? " audio" : ""));
      label.dataset.i = String(f.i);
      if (f.have) label.dataset.have = f.have;
      if (f.blocked && !f.have) label.dataset.blocked = "1";
      const tick = element("input");
      tick.type = "checkbox";
      tick.value = String(f.i);
      tick.disabled = f.have === "whole" || f.have === "other" || !!f.blocked;
      tick.checked = !tick.disabled && f.kind === "video" && !manyDubs();
      const quality = f.kind === "audio" ? (f.title || f.name)
        : f.quality || (f.size ? (f.stream ? "≈ " : "") + megabytes(f.size) : "");
      const said = f.blocked && !f.have ? "нет видео" : HAVE_MARKS[f.have] || "";
      label.append(tick, element("span", "vtile-n", f.label || "•"), element("span", "vtile-q", quality),
        element("span", "vtile-t", f.length ? clock(f.length) : ""), element("span", "vtile-mark", said));
      const bar = element("span", "vtile-bar");
      bar.append(element("i"));
      label.append(bar);
      label.title = [f.title, f.note, (f.group ? f.group + " / " : "") + f.name,
        f.size ? (f.stream ? "≈ " : "") + megabytes(f.size) : "", f.blocked ? "Не скачать: " + f.blocked : ""]
        .filter(Boolean).join("\n");
      tick.setAttribute("aria-label", [f.kind === "audio" ? "Звук" : "Серия " + (f.label || ""), quality,
        f.length ? clock(f.length) : "", said].filter(Boolean).join(", "));
      return label;
    }

    function showTiles() {
      list.textContent = "";
      if (manyDubs()) {
        renderDubs();
        return;
      }
      if (dubs.bar && qualityBox.parentElement !== dubs.bar.parent) {     // the plates' «Качество» back in the bar
        dubs.bar.parent.insertBefore(qualityBox, dubs.bar.next);
      }
      const groups = new Map();
      for (const f of found.files) {
        const key = f.kind === "audio" ? "\u0000звук" : f.group || "";
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(f);
      }
      const keys = [...groups.keys()].sort((a, b) => (a === "\u0000звук") - (b === "\u0000звук"));
      for (const key of keys) {
        const files = groups.get(key);
        const section = element("section", "vgroup");
        if (keys.length > 1 || key) {
          const head = element("div", "vgroup-head");
          const sounds = key === "\u0000звук";
          head.append(element("h4", "", sounds ? "Звук" : key || "Серии"),
            element("span", "vgroup-count", plural(files.length, sounds ? ["файл", "файла", "файлов"]
              : ["серия", "серии", "серий"])));
          const toggle = element("button", "link", "отметить все");
          toggle.type = "button";
          toggle.dataset.group = key;
          head.append(toggle);
          section.append(head);
        }
        const grid = element("div", "vtiles");
        files.forEach((f) => grid.append(tile(f)));
        section.append(grid);
        list.append(section);
      }
      foundBox.querySelector(".vpick-head h3").textContent = found.files.every((f) => f.kind === "audio") ? "Файлы"
        : found.files.some((f) => f.kind === "audio") ? "Серии и звук" : "Серии";
    }

    // ── every voice-over of a title: «Озвучка», «Качество», «Субтитры» — and the episodes ──
    function dubGroups() {
      const groups = new Map();
      for (const f of found.files) {
        const name = f.group || "";
        if (!groups.has(name)) groups.set(name, { name, files: [], subs: isSubs(name) });
        groups.get(name).files.push(f);
      }
      return [...groups.values()];
    }

    function groupFacts(g) {
      const best = Math.max(0, ...g.files.map((f) => parseInt(String(f.quality || "").replace(/\D+/g, ""), 10) || 0));
      const players = [...new Set(g.files.flatMap((f) => String(f.note || "").replace("Плеер: ", "").split(", ")))]
        .filter(Boolean);
      return (best ? "до " + best + "p" : "") + (players.length ? " · " + players.join(", ") : "");
    }

    // a release's kind by its name: a dubbing (the voices replaced), a voice-over (read over them), subtitles
    const KINDS = [["dub", "Дубляж"], ["voice", "Озвучка"], ["subs", "Субтитры"]];
    const kindOf = (name) => (/^дубляж/i.test(name || "") ? "dub" : isSubs(name) ? "subs" : "voice");
    const shortName = (name) => String(name || "").replace(/^(озвучка|дубляж|субтитры)\s+/i, "") || name || "";

    function studioPlate(groups) {
      const kind = dubs.kind;
      const box = element("div", "vplate");
      box.dataset.plate = "studios";
      const now = element("button", "vplate-now");
      now.type = "button";
      now.setAttribute("aria-expanded", "false");
      now.append(element("span", "vplate-label", (KINDS.find((k) => k[0] === kind) || KINDS[1])[1]),
        element("b", "vplate-value", ""));
      const menu = element("div", "vplate-menu");
      menu.hidden = true;
      const tools = element("div", "vplate-tools");
      const all = element("button", "link", "отметить все");
      all.type = "button";
      all.dataset.plateAll = kind;
      const none = element("button", "link", "снять все");
      none.type = "button";
      none.dataset.plateNone = kind;
      tools.append(all, none);
      menu.append(tools);
      if (kind === "subs") {
        menu.append(element("p", "vplate-note", "Слова субтитров вшиты в картинку, поэтому каждый релиз — отдельное "
          + "видео: в общий файл серии он не собирается и весит как серия целиком."));
      }
      for (const g of groups.filter((x) => kindOf(x.name) === kind)) {
        const row = element("label", "vplate-row");
        const tick = element("input");
        tick.type = "checkbox";
        tick.value = g.name;
        tick.checked = dubs.groups.has(g.name);
        const had = g.files.filter((f) => f.have === "whole").length;
        row.append(tick, element("span", "vplate-name", shortName(g.name)), element("span", "vplate-meta", groupFacts(g)),
          element("span", "vplate-count", (had ? "есть " + had + " из " : "") + plural(g.files.length, ["серия", "серии", "серий"])));
        row.title = g.name;
        menu.append(row);
      }
      box.append(now, menu);
      return box;
    }

    function renderDubs() {
      const groups = dubGroups();
      const present = KINDS.filter(([kind]) => groups.some((g) => kindOf(g.name) === kind));
      if (!present.some(([kind]) => kind === dubs.kind)) dubs.kind = (present.find(([k]) => k === "voice") || present[0] || ["voice"])[0];
      const plates = element("div", "vplates");
      // the kind: «Дубляж», «Озвучка», «Субтитры» — the list of studios beside it is of the kind chosen
      const kinds = element("div", "vkinds");
      kinds.setAttribute("role", "tablist");
      kinds.setAttribute("aria-label", "Что выбирать");
      for (const [kind, title] of present) {
        const button = element("button", "vkind", "");
        button.type = "button";
        button.setAttribute("role", "tab");
        button.dataset.kind = kind;
        button.setAttribute("aria-selected", kind === dubs.kind ? "true" : "false");
        button.append(element("span", "", title), element("span", "vkind-count",
          String(groups.filter((g) => kindOf(g.name) === kind).length)));
        kinds.append(button);
      }
      plates.append(kinds, studioPlate(groups));
      const qualityPlate = element("div", "vplate vplate-quality");
      qualityPlate.dataset.plate = "quality";
      if (!dubs.bar) dubs.bar = { parent: qualityBox.parentElement, next: qualityBox.nextSibling };
      qualityPlate.append(qualityBox);
      plates.append(qualityPlate);
      list.append(plates);
      const note = element("p", "vplate-warn", "");
      note.hidden = true;
      list.append(note);
      const labels = [...new Set(found.files.map((f) => f.label || ""))].sort(natural);
      const grid = element("div", "vtiles vtiles-episodes");
      for (const label of labels) {
        const tile = element("label", "vtile vep-tile");
        tile.dataset.label = label;
        const tick = element("input");
        tick.type = "checkbox";
        tick.value = label;
        tick.checked = dubs.episodes.has(label);
        tile.append(tick, element("span", "vtile-n", label || "•"), element("span", "vtile-q", ""),
          element("span", "vtile-t", ""), element("span", "vtile-mark", ""));
        const bar = element("span", "vtile-bar");
        bar.append(element("i"));
        tile.append(bar);
        grid.append(tile);
      }
      list.append(grid);
      foundBox.querySelector(".vpick-head h3").textContent = "Серии";
      paintDubs();
    }

    function chosenOf(kind) {
      return [...dubs.groups].filter((name) => kindOf(name) === kind);
    }

    // the plates say what is chosen; each episode's tile — how much of it the chosen releases have
    function paintDubs() {
      const groups = dubGroups();
      const box = list.querySelector(".vplate[data-plate='studios']");
      if (box) {
        const mine = groups.filter((g) => kindOf(g.name) === dubs.kind);
        const on = mine.filter((g) => dubs.groups.has(g.name));
        box.querySelector(".vplate-value").textContent = !on.length ? (dubs.kind === "subs" ? "нет" : "не выбрана")
          : on.length === 1 ? shortName(on[0].name) : on.length + " из " + mine.length;
        box.classList.toggle("chosen", !!on.length);
      }
      for (const button of list.querySelectorAll(".vkind")) {
        const n = chosenOf(button.dataset.kind).length;
        button.classList.toggle("chosen", n > 0);
        button.querySelector(".vkind-count").textContent = n ? n + " из " + groups.filter((g) => kindOf(g.name) === button.dataset.kind).length
          : String(groups.filter((g) => kindOf(g.name) === button.dataset.kind).length);
      }
      const warn = list.querySelector(".vplate-warn");
      const subsOn = chosenOf("subs").length > 0;
      warn.hidden = !subsOn;
      warn.textContent = subsOn ? "Субтитры — отдельными видео: слова вшиты в картинку, видеоряд у них свой, поэтому "
        + "с ними скачается и займёт больше." : "";
      for (const tile of list.querySelectorAll(".vep-tile")) {
        const files = found.files.filter((f) => (f.label || "") === tile.dataset.label && dubs.groups.has(f.group || ""));
        const had = files.filter((f) => f.have === "whole" || f.have === "other").length;
        const tick = tile.querySelector("input");
        tick.disabled = !files.length || had === files.length;
        if (tick.disabled) tick.checked = false;
        tile.dataset.have = files.length && had === files.length ? "whole" : had ? "part" : "";
        tile.querySelector(".vtile-q").textContent = files.length ? plural(files.length, ["релиз", "релиза", "релизов"]) : "";
        tile.querySelector(".vtile-mark").textContent = !dubs.groups.size ? "" : !files.length ? "нет у выбранных"
          : had === files.length ? "уже есть" : had ? "есть " + had + " из " + files.length : "";
        tile.title = files.map((f) => (f.group || "") + (f.have === "whole" ? " — есть" : "")).join("\n");
      }
      dubs.episodes = new Set([...list.querySelectorAll(".vep-tile input:checked")].map((x) => x.value));
      paintEpisodesRun();
    }

    function paintEpisodesRun() {
      for (const tile of list.querySelectorAll(".vep-tile")) {
        const mine = Object.keys(runStates).filter((i) => (found.files[Number(i)] || {}).label === tile.dataset.label);
        if (!mine.length) { delete tile.dataset.dl; tile.style.setProperty("--p", "0%"); continue; }
        const states = mine.map((i) => runStates[i]).map((s) => (s === "queued" && !keepQueued() ? "" : s));
        const active = mine.find((i) => runStates[i] === "active" || runStates[i] === "paused");
        const pausedHere = active && runStates[active] === "paused";
        const left = states.filter((s) => s === "queued" || s === "active" || s === "paused").length;
        const bad = states.filter((s) => s === "failed" || s === "dead").length;
        const state = active ? "active" : left ? "queued" : bad ? "failed" : states.every((s) => !s) ? "" : "done";
        if (state) tile.dataset.dl = state === "active" && pausedHere ? "paused" : state; else delete tile.dataset.dl;
        const part = active && runActive.file === Number(active) ? runActive.part : 0;
        const done = states.filter((s) => s === "done" || s === "whole").length;
        const mark = tile.querySelector(".vtile-mark");
        if (state === "active") mark.textContent = (pausedHere ? "пауза " : "качается ") + (done + 1) + " из " + mine.length
          + (part ? " · " + Math.floor(part * 100) + "%" : "");
        else if (state === "queued") mark.textContent = "в очереди";
        else if (state === "failed") mark.textContent = "не скачались: " + bad;
        else if (state === "done") mark.textContent = "скачана";
        tile.style.setProperty("--p", state === "active" ? Math.round(((done + part) / mine.length) * 100) + "%" : "0%");
      }
    }

    // what the chosen ones take on the disk: one episode measured in each quality (/api/videos/measure)
    function measure() {
      if (dubs.measured || dubs.measuring === findId) return;
      dubs.measuring = findId;
      const mine = findId;
      fetch("/api/videos/measure?find=" + encodeURIComponent(mine), { credentials: "same-origin" })
        .then((r) => r.json()).then((reply) => {
          if (mine !== findId) return;
          dubs.measured = (reply && reply.measured) || {};
          countChosen();
        }).catch(() => { if (mine === findId) { dubs.measured = {}; countChosen(); } });
    }

    function estimate(files) {
      const m = dubs.measured;
      if (!m || !m.sizes || !m.seconds) return 0;
      const heights = Object.keys(m.sizes).map(Number).sort((a, b) => b - a);
      const cap = qualityPick.value === "best" ? 1e9 : Number(qualityPick.value) || 1e9;
      const sizeAt = (limit) => {
        const h = heights.find((x) => x <= Math.min(cap, limit)) || heights[heights.length - 1];
        return m.sizes[String(h)] || 0;
      };
      const limitOf = (f) => (/CVH/.test(f.note || "") ? 1e9 : /AniLiberty/.test(f.note || "") ? 1080 : 720);
      const sound = 14000 * m.seconds;          // a voice-over's sound alone: ~110 kbit/s
      const merge = mergeTick.checked;
      let total = 0;
      const episodes = new Map();
      for (const f of files) {
        if (!episodes.has(f.label)) episodes.set(f.label, []);
        episodes.get(f.label).push(f);
      }
      for (const [label, mine] of episodes) {
        const voices = mine.filter((f) => !isSubs(f.group));
        total += mine.filter((f) => isSubs(f.group)).reduce((s, f) => s + sizeAt(limitOf(f)), 0);
        if (!voices.length) continue;
        if (!merge) { total += voices.reduce((s, f) => s + sizeAt(limitOf(f)), 0); continue; }
        const had = found.files.some((f) => f.label === label && !isSubs(f.group) && f.have === "whole");
        total += (had ? 0 : sizeAt(Math.max(...voices.map(limitOf)))) + (voices.length - (had ? 0 : 1)) * sound;
      }
      return total;
    }

    function showFound(st) {
      found = st && st.found;
      if (!found) { foundBox.hidden = true; return; }
      if (st.id !== dubs.find) {     // a new list: the voice-overs begun in the folder chosen, no episode yet
        dubs.find = st.id;
        dubs.measured = null;
        dubs.episodes = new Set();
        dubs.groups = new Set((found.files || []).filter((f) => f.have === "whole" || f.have === "part")
          .map((f) => f.group || ""));
        const begun = [...dubs.groups][0];
        dubs.kind = begun !== undefined ? kindOf(begun) : "voice";
      }
      findId = st.id;
      foundBox.hidden = false;
      if (manyDubs()) measure();
      qualityBox.hidden = !manyDubs();
      mergeBox.hidden = !manyDubs();
      foundBox.classList.toggle("many-dubs", manyDubs());
      lastPicked = -1;
      showCard(st);
      showTiles();
      paintRun();
      countChosen();
    }

    function chosen() {
      if (manyDubs()) {              // each episode ticked of each release ticked — not the ones there already
        return found.files.filter((f) => dubs.episodes.has(f.label || "") && dubs.groups.has(f.group || "")
          && f.have !== "whole" && f.have !== "other" && !f.blocked).map((f) => f.i);
      }
      return [...list.querySelectorAll(".vtile input:checked")].map((t) => Number(t.value));
    }

    // another title downloads now: this one goes after it
    const goWord = () => (downloading() && !runMine ? "В очередь: " : "Скачать ");

    function countChosen() {
      if (!found) return;
      if (manyDubs()) {
        const files = chosen().map((i) => found.files[i]).filter(Boolean);
        const open = [...list.querySelectorAll(".vep-tile input")].filter((x) => !x.disabled).length;
        const said = [["dub", ["дубляж", "дубляжа", "дубляжей"]], ["voice", ["озвучка", "озвучки", "озвучек"]],
          ["subs", ["релиз субтитров", "релиза субтитров", "релизов субтитров"]]]
          .map(([kind, forms]) => [chosenOf(kind).length, forms]).filter(([n]) => n).map(([n, forms]) => plural(n, forms));
        goButton.disabled = !files.length || busyHere();
        goButton.textContent = files.length ? goWord() + plural(files.length, ["файл", "файла", "файлов"])
          : !dubs.groups.size ? "Выберите озвучку" : "Отметьте серии";
        const folderName = String(found.folder || "").split(/[\\/]/).filter(Boolean).pop() || "";
        const weight = files.length ? estimate(files) : 0;
        sum.textContent = (files.length && dubs.measured === null ? "считаю вес…, "
          : weight ? "≈ " + megabytes(weight) + " на диске, " : "") + "в папку «" + folderName + "»";
        pickCount.textContent = (said.length ? said.join(", ") : "ничего не выбрано") + " · серий отмечено "
          + dubs.episodes.size + " из " + list.querySelectorAll(".vep-tile").length;
        foundBox.classList.toggle("all-had", !open && dubs.groups.size > 0);
        return;
      }
      const files = chosen().map((i) => found.files[i]).filter(Boolean);
      const open = tiles().filter((t) => !t.querySelector("input").disabled).length;
      const videos = files.filter((f) => f.kind === "video").length;
      const what = videos === files.length ? plural(files.length, ["серию", "серии", "серий"])
        : plural(files.length, ["файл", "файла", "файлов"]);
      goButton.disabled = !files.length || busyHere();
      goButton.textContent = files.length ? goWord() + what : "Отметьте серии";
      const bytes = files.reduce((total, f) => total + (Number(f.size) || 0), 0);
      const folderName = String(found.folder || "").split(/[\\/]/).filter(Boolean).pop() || "";
      sum.textContent = (bytes ? (files.some((f) => f.stream) ? "≈ " : "") + megabytes(bytes) + ", " : "")
        + "в папку «" + folderName + "»";
      pickCount.textContent = open ? "отмечено " + files.length + " из " + open
        : "все уже скачаны";
      foundBox.classList.toggle("all-had", !open);
    }

    const closePlates = (except) => {
      for (const box of list.querySelectorAll(".vplate")) {
        if (box === except) continue;
        const menu = box.querySelector(".vplate-menu");
        if (menu && !menu.hidden) {
          menu.hidden = true;
          box.querySelector(".vplate-now").setAttribute("aria-expanded", "false");
        }
      }
    };
    document.addEventListener("click", (event) => { if (!event.target.closest(".vplate")) closePlates(null); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closePlates(null); });
    list.addEventListener("click", (event) => {
      if (manyDubs()) {
        const kind = event.target.closest(".vkind[data-kind]");
        if (kind) {                  // another kind: its studios in the plate beside (the ones chosen stay chosen)
          event.preventDefault();
          dubs.kind = kind.dataset.kind;
          for (const b of list.querySelectorAll(".vkind")) b.setAttribute("aria-selected", b === kind ? "true" : "false");
          const old = list.querySelector(".vplate[data-plate='studios']");
          old.replaceWith(studioPlate(dubGroups()));
          paintDubs();
          countChosen();
          return;
        }
        const now = event.target.closest(".vplate-now");
        if (now) {
          event.preventDefault();
          const box = now.closest(".vplate");
          const menu = box.querySelector(".vplate-menu");
          closePlates(box);
          menu.hidden = !menu.hidden;
          now.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
          return;
        }
        const all = event.target.closest("[data-plate-all], [data-plate-none]");
        if (all) {
          event.preventDefault();
          for (const tick of all.closest(".vplate-menu").querySelectorAll(".vplate-row input")) {
            tick.checked = !!all.dataset.plateAll;
            if (tick.checked) dubs.groups.add(tick.value); else dubs.groups.delete(tick.value);
          }
          paintDubs();
          countChosen();
          return;
        }
        const tick = event.target.closest(".vep-tile input");
        if (tick && event.shiftKey && lastPicked >= 0) {     // shift: the episodes from the last one ticked to this
          const all = [...list.querySelectorAll(".vep-tile input")];
          const at = all.indexOf(tick);
          const [from, to] = at < lastPicked ? [at, lastPicked] : [lastPicked, at];
          all.slice(from, to + 1).forEach((x) => { if (!x.disabled) x.checked = !tick.checked ? false : true; });
        }
        if (tick) lastPicked = [...list.querySelectorAll(".vep-tile input")].indexOf(tick);
        return;
      }
      const group = event.target.closest("button[data-group]");
      if (group) {
        event.preventDefault();
        const boxes = [...group.closest(".vgroup").querySelectorAll(".vtile input")].filter((t) => !t.disabled);
        const on = boxes.some((t) => !t.checked);
        boxes.forEach((t) => { t.checked = on; });
        countChosen();
        return;
      }
      const tick = event.target.closest(".vtile input");
      if (!tick) return;
      const all = [...list.querySelectorAll(".vtile input")];
      const at = all.indexOf(tick);
      if (event.shiftKey && lastPicked >= 0 && lastPicked !== at) {
        const [from, to] = at < lastPicked ? [at, lastPicked] : [lastPicked, at];
        all.slice(from, to + 1).forEach((t) => {
          if (!t.disabled) t.checked = tick.checked;
        });
      }
      lastPicked = at;
    });
    list.addEventListener("change", (event) => {
      if (manyDubs()) {
        const tick = event.target;
        if (tick.closest(".vplate-row")) {
          if (tick.checked) dubs.groups.add(tick.value); else dubs.groups.delete(tick.value);
          paintDubs();
        }
        dubs.episodes = new Set([...list.querySelectorAll(".vep-tile input:checked")].map((x) => x.value));
      }
      countChosen();
    });
    qualityPick.addEventListener("change", () => { if (manyDubs()) countChosen(); });
    mergeTick.addEventListener("change", () => { if (manyDubs()) countChosen(); });
    foundBox.querySelector(".vpick-tools").addEventListener("click", (event) => {
      const how = event.target.closest("[data-pick]");
      if (!how) return;
      if (manyDubs()) {              // the episodes: all of them, none, the ones the chosen releases do not have yet
        for (const tick of list.querySelectorAll(".vep-tile input")) {
          if (tick.disabled) continue;
          tick.checked = how.dataset.pick === "all"
            || (how.dataset.pick === "new" && tick.closest(".vep-tile").dataset.have === "");
        }
        dubs.episodes = new Set([...list.querySelectorAll(".vep-tile input:checked")].map((x) => x.value));
        countChosen();
        return;
      }
      for (const t of list.querySelectorAll(".vtile input")) {
        if (t.disabled) continue;
        const audio = t.closest(".vtile").classList.contains("audio");
        t.checked = how.dataset.pick === "all" || (how.dataset.pick === "new" && !audio);
      }
      countChosen();
    });

    // ── the download: the tiles show each file's way, the bar the whole of it ──
    function paintRun() {
      if (manyDubs()) {
        if (found) paintEpisodesRun();
        return;
      }
      for (const t of tiles()) {
        const i = t.dataset.i;
        let state = runStates[i] || "";
        if (state === "queued" && !keepQueued()) state = "";       // stopped before its turn: as it was
        if (state) t.dataset.dl = state; else delete t.dataset.dl;
        const mark = t.querySelector(".vtile-mark");
        const f = found && found.files[Number(i)];
        if ((state === "active" || state === "paused") && runActive.file === Number(i) && runActive.part) {
          mark.textContent = (state === "paused" ? "пауза " : "качается ") + Math.floor(runActive.part * 100) + "%";
        } else if (state) {
          mark.textContent = RUN_MARKS[state] || state;
        } else if (f) {
          mark.textContent = f.blocked && !f.have ? "нет видео" : HAVE_MARKS[f.have] || "";
        }
        t.style.setProperty("--p", state === "active" || state === "paused" ? Math.round(runActive.part * 100) + "%" : "0%");
      }
    }

    function showMeter(m) {
      if (!m || !m.count) {
        meterBox.hidden = true;
        runActive = { file: -1, part: 0 };
        return;
      }
      meterBox.hidden = false;
      if (m.merging) {                 // after the downloads: each episode's voice-overs into its file
        runActive = { file: -1, part: 0 };
        meterBox.querySelector("progress").value = Math.round(((Number(m.index) || 1) - 1) / m.count * 1000);
        meterBox.querySelector(".vmeter-text").textContent = "Собираю в общие файлы серий: " + (m.name || "")
          + ". Всего: " + m.index + " из " + m.count;
        return;
      }
      const part = m.total ? Math.min(1, m.done / m.total) : 0;
      runActive = { file: Number(m.file), part };
      const whole = Math.min(1, ((Number(m.index) || 1) - 1 + part) / m.count);
      meterBox.querySelector("progress").value = Math.round(whole * 1000);
      const f = runMine && found && found.files[Number(m.file)];
      const name = f && f.kind === "video" && f.label ? "Серия " + f.label
        : !runMine && m.name ? String(m.name).replace(/\.[^.]+$/, "") : "Файл";
      const bits = [];
      if (m.total) bits.push(Math.floor(part * 100) + "% (" + megabytes(m.done) + " из " + megabytes(m.total) + ")");
      else if (m.done) bits.push(megabytes(m.done));
      if (m.speed) bits.push(megabytes(m.speed) + "/с");
      meterBox.querySelector(".vmeter-text").textContent = (m.paused ? "Пауза — " : "") + name
        + (bits.length ? ": " + bits.join(", ") : "") + ". Всего: " + m.index + " из " + m.count;
    }

    function showResult(st, busy) {
      const sumUp = st.summary || {};
      if (busy || !st.id || !runMine || !["done", "stopped", "failed"].includes(st.status)) {
        result.hidden = true;
        return;
      }
      const done = Number(sumUp.done) || 0, failed = Number(sumUp.failed_files) || 0, had = Number(sumUp.whole) || 0;
      const videos = found && found.files.every((f) => f.kind === "video");
      let text = st.status === "failed" ? (st.error || "Скачивание прервалось — причина в подробностях")
        : (st.status === "stopped" || sumUp.stopped ? "Остановлено: " : "Готово: ")
          + (done ? "скачано " + plural(done, videos ? ["серия", "серии", "серий"] : ["файл", "файла", "файлов"])
            : "ничего не скачано");
      if (st.status !== "failed" && had) text += ", уже были: " + had;
      if (st.status !== "failed" && failed) text += ", не скачались: " + failed + " — причины в подробностях";
      const merged = Number(sumUp.merged) || 0;
      if (st.status !== "failed" && merged) text += ", собрано в общие файлы: " + plural(merged, ["серия", "серии", "серий"]);
      result.textContent = text;
      result.className = "vget-result " + (st.status === "failed" || (failed && !done) ? "bad" : failed || sumUp.stopped ? "part" : "ok");
      result.hidden = false;
    }

    function watchFind() {
      findWatch = findWatch || LB.watchTask(findJob, "/api/videos/find-state", {
        onState(st, busy) {
          findButtons().forEach((b) => { b.disabled = busy; });
          showStep(st, busy);
          if (!busy && st.found && st.id !== findId) showFound(st);
          if (busy) foundBox.hidden = true;
        },
      });
      return findWatch;
    }

    function watchGet() {
      getWatch = getWatch || LB.watchTask(getJob, "/api/videos/download-state", {
        onState(st, busy) {
          // a download of another list (another title found while it goes) is not this list's: its tiles stay
          // as they are, the bar says what goes; this list waiting in the queue — its tiles «в очереди»
          const mine = !!st.id && !!st.find && String(st.find) === String(findId);
          const queue = st.queue || [];
          runMine = mine;
          queueHere = queue.find((q) => String(q.find) === String(findId)) || null;
          foundBox.classList.toggle("vrun-other", !!st.id && !mine);
          foundBox.classList.toggle("vrun-busy", busy);
          foundBox.classList.toggle("downloading", busy && mine);
          stopButton.hidden = !busy;
          stopButton.textContent = busy && queue.length ? "Остановить всё" : "Остановить";
          pauseButton.hidden = !busy || st.status === "stopping";
          pauseButton.textContent = st.paused ? "Продолжить" : "Пауза";
          pauseButton.classList.toggle("primary", !!st.paused);
          getJob.classList.toggle("paused", busy && !!st.paused);
          showMeter(busy ? st.meter : null);
          runStates = mine ? Object.assign({}, st.states || {}) : {};
          if (queueHere) queueHere.chosen.forEach((i) => { if (!runStates[i]) runStates[i] = "queued"; });
          paintRun();
          showQueue(queue);
          showResult(st, busy);
          openLink.hidden = busy || !mine || !st.series_url;
          if (st.series_url) openLink.querySelector("a").href = st.series_url;
          countChosen();
        },
        onEnd() {
          meterBox.hidden = true;
          say(stopButton, "");
          // the list shows what is in the folder now (the files downloaded are «есть»)
          fetch("/api/videos/find-state", { credentials: "same-origin" }).then((r) => r.json())
            .then((st) => { if (st.found) { findId = 0; showFound(st); } }).catch(() => {});
        },
      });
      return getWatch;
    }

    function showQueue(queue) {
      queueBox.textContent = "";
      queueBox.hidden = !queue.length;
      queue.forEach((q, n) => {
        const li = element("li", String(q.find) === String(findId) ? "here" : "");
        li.append(element("span", "vqueue-what", (n ? "Потом" : "Дальше") + ": «" + q.title + "» — "
          + plural(q.files, ["файл", "файла", "файлов"]) + (String(q.find) === String(findId) ? " (этот список)" : "")));
        const drop = element("button", "link vqueue-drop", "убрать");
        drop.type = "button";
        drop.addEventListener("click", () => postJson("/api/videos/download/unqueue", { id: q.id })
          .then(() => { queueHere = null; watchGet().poll(); }).catch((err) => say(drop, err.message, true)));
        li.append(drop);
        queueBox.append(li);
      });
    }

    function searching(button) {
      lastLine = "";
      step.textContent = "Читаю страницу…";
      step.classList.remove("error");
      step.hidden = false;
      foundBox.hidden = true;
      findJob.hidden = false;
      watchFind().started();
      say(button, "");
    }

    // another title of the franchise (the viewing order, a season hung on a recap): its address searched as if pasted
    function findOther(address) {
      const form = box.querySelector('[data-form="vid-find-url"]');
      form.url.value = address;
      form.requestSubmit();
      form.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    box.querySelector('[data-form="vid-find-url"]').addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector('button[type="submit"]');
      const address = form.url.value.trim();
      if (!address) { say(button, "Вставьте ссылку на страницу тайтла", true); form.url.focus(); return; }
      try {
        await postJson("/api/videos/find", { url: address });
        searching(button);
      } catch (err) {
        say(button, err.message, true);
      }
    });

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      say(fileInput, "Отправляю файл…");
      try {
        const response = await fetch("/api/videos/find-file?name=" + encodeURIComponent(file.name), {
          method: "POST", credentials: "same-origin", body: file,
          headers: { "Content-Type": "application/octet-stream" },
        });
        const reply = await response.json().catch(() => ({ ok: false, error: "Ответ " + response.status }));
        if (!response.ok || !reply.ok) throw new Error(reply.error || "Ответ " + response.status);
        searching(fileInput);
      } catch (err) {
        say(fileInput, err.message, true);
      } finally {
        fileInput.value = "";
      }
    });

    actions["vid-download"] = async (el) => {
      const files = chosen();
      if (!files.length) { say(el, "Отметьте серии, которые скачать", true); return; }
      const quality = manyDubs() ? qualityPick.value : "";
      const merge = manyDubs() && mergeTick.checked;
      try {
        const reply = await postJson("/api/videos/download", { find: findId, files, quality, merge });
        if (reply && reply.queued) {               // another download goes: this one waits for its turn
          say(el, (reply.queued > 1 ? "В очереди " + reply.queued + "-м" : "В очереди следующим")
            + ": начнётся само, когда закончится текущее скачивание");
          queueHere = { id: reply.queue_id, find: findId, chosen: files };
          files.forEach((i) => { runStates[i] = "queued"; });
          paintRun();
          getJob.hidden = false;
          watchGet().poll();
          return;
        }
        say(el, "");
        runStates = {};
        files.forEach((i) => { runStates[i] = "queued"; });
        paintRun();
        foundBox.classList.add("downloading");
        getJob.hidden = false;
        openLink.hidden = true;
        result.hidden = true;
        watchGet().started();
      } catch (err) {
        say(el, err.message, true);
      }
    };

    try {                            // the quality chosen before (this browser)
      const kept = localStorage.getItem("lb.video-dub-quality");
      if (kept && [...qualityPick.options].some((o) => o.value === kept)) qualityPick.value = kept;
    } catch (_) { /* the best, this time */ }
    qualityPick.addEventListener("change", () => {
      try { localStorage.setItem("lb.video-dub-quality", qualityPick.value); } catch (_) { /* only this time */ }
    });

    actions["vid-download-pause"] = (el) => postJson("/api/videos/download/pause", { pause: el.textContent !== "Продолжить" })
      .then((reply) => {
        el.textContent = reply.paused ? "Продолжить" : "Пауза";
        say(el, reply.paused ? "Ставлю на паузу…" : "");
        if (getWatch) getWatch.poll();
      }).catch((err) => say(el, err.message, true));

    actions["vid-download-stop"] = (el) => postJson("/api/videos/download/stop", {})
      .then(() => say(el, "Останавливаю…")).catch((err) => say(el, err.message, true));

    // a search or a download that goes on (or just ended) when the page opens; a title to find from another page
    // («Порядок просмотра» of a series: «найти и скачать») — searched at once, unless a search goes
    fetch("/api/videos/find-state", { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => {
        if (st.id) { findJob.hidden = false; watchFind(); }
        if (box.dataset.autofind === "1" && !RUNNING.includes(st.status || "")) {
          delete box.dataset.autofind;
          box.querySelector('[data-form="vid-find-url"]').requestSubmit();
        }
      }).catch(() => {});
    fetch("/api/videos/download-state", { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => { if (st.id) { getJob.hidden = false; watchGet(); } }).catch(() => {});
  }
})();
