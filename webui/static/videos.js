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
  const RUN_MARKS = { queued: "в очереди", active: "качается", done: "скачана", whole: "уже была", other: "свой файл",
    failed: "не скачалась", dead: "пустая на сайте", stopped: "остановлена" };
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
    const meterBox = getJob.querySelector(".vmeter");
    const result = getJob.querySelector(".vget-result");
    const openLink = getJob.querySelector(".vget-open");
    const fileInput = box.querySelector("#vid-find-file");
    let findId = 0;
    let found = null;                // the list shown (the search's result)
    let lastLine = "";               // the search's newest line: what it does now
    let lastPicked = -1;             // shift+click: the tiles from this one to the clicked one
    let runStates = {};              // file number -> how its download went (the download of this list)
    let runActive = { file: -1, part: 0 };
    let findWatch = null;
    let getWatch = null;

    const findButtons = () => box.querySelectorAll('.vget-ask button[type="submit"], .vget-ask input[type="file"]');
    const downloading = () => RUNNING.includes(getJob.dataset.status || "");
    const tiles = () => [...list.querySelectorAll(".vtile")];

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
      }
      const voices = found.voices || [];
      if (sources.length && voices.length) {
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
      tick.checked = !tick.disabled && f.kind === "video";
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

    function showFound(st) {
      found = st && st.found;
      if (!found) { foundBox.hidden = true; return; }
      findId = st.id;
      foundBox.hidden = false;
      lastPicked = -1;
      showCard(st);
      showTiles();
      paintRun();
      countChosen();
    }

    function chosen() {
      return [...list.querySelectorAll(".vtile input:checked")].map((t) => Number(t.value));
    }

    function countChosen() {
      if (!found) return;
      const files = chosen().map((i) => found.files[i]).filter(Boolean);
      const open = tiles().filter((t) => !t.querySelector("input").disabled).length;
      const videos = files.filter((f) => f.kind === "video").length;
      const what = videos === files.length ? plural(files.length, ["серию", "серии", "серий"])
        : plural(files.length, ["файл", "файла", "файлов"]);
      goButton.disabled = !files.length || downloading();
      goButton.textContent = files.length ? "Скачать " + what : "Отметьте серии";
      const bytes = files.reduce((total, f) => total + (Number(f.size) || 0), 0);
      const folderName = String(found.folder || "").split(/[\\/]/).filter(Boolean).pop() || "";
      sum.textContent = (bytes ? (files.some((f) => f.stream) ? "≈ " : "") + megabytes(bytes) + ", " : "")
        + "в папку «" + folderName + "»";
      pickCount.textContent = open ? "отмечено " + files.length + " из " + open
        : "все уже скачаны";
      foundBox.classList.toggle("all-had", !open);
    }

    list.addEventListener("click", (event) => {
      const group = event.target.closest("button[data-group]");
      if (group) {
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
        all.slice(from, to + 1).forEach((t) => { if (!t.disabled) t.checked = tick.checked; });
      }
      lastPicked = at;
    });
    list.addEventListener("change", countChosen);
    foundBox.querySelector(".vpick-tools").addEventListener("click", (event) => {
      const how = event.target.closest("[data-pick]");
      if (!how) return;
      for (const t of list.querySelectorAll(".vtile input")) {
        if (t.disabled) continue;
        const audio = t.closest(".vtile").classList.contains("audio");
        t.checked = how.dataset.pick === "all" || (how.dataset.pick === "new" && !audio);
      }
      countChosen();
    });

    // ── the download: the tiles show each file's way, the bar the whole of it ──
    function paintRun() {
      for (const t of tiles()) {
        const i = t.dataset.i;
        let state = runStates[i] || "";
        if (state === "queued" && !downloading()) state = "";       // stopped before its turn: as it was
        if (state) t.dataset.dl = state; else delete t.dataset.dl;
        const mark = t.querySelector(".vtile-mark");
        const f = found && found.files[Number(i)];
        if (state === "active" && runActive.file === Number(i) && runActive.part) {
          mark.textContent = "качается " + Math.floor(runActive.part * 100) + "%";
        } else if (state) {
          mark.textContent = RUN_MARKS[state] || state;
        } else if (f) {
          mark.textContent = f.blocked && !f.have ? "нет видео" : HAVE_MARKS[f.have] || "";
        }
        t.style.setProperty("--p", state === "active" ? Math.round(runActive.part * 100) + "%" : "0%");
      }
    }

    function showMeter(m) {
      if (!m || !m.count) {
        meterBox.hidden = true;
        runActive = { file: -1, part: 0 };
        return;
      }
      meterBox.hidden = false;
      const part = m.total ? Math.min(1, m.done / m.total) : 0;
      runActive = { file: Number(m.file), part };
      const whole = Math.min(1, ((Number(m.index) || 1) - 1 + part) / m.count);
      meterBox.querySelector("progress").value = Math.round(whole * 1000);
      const f = found && found.files[Number(m.file)];
      const name = f && f.kind === "video" && f.label ? "Серия " + f.label : "Файл";
      const bits = [];
      if (m.total) bits.push(Math.floor(part * 100) + "% (" + megabytes(m.done) + " из " + megabytes(m.total) + ")");
      else if (m.done) bits.push(megabytes(m.done));
      if (m.speed) bits.push(megabytes(m.speed) + "/с");
      meterBox.querySelector(".vmeter-text").textContent = name + (bits.length ? ": " + bits.join(", ") : "")
        + ". Всего: " + m.index + " из " + m.count;
    }

    function showResult(st, busy) {
      const sumUp = st.summary || {};
      if (busy || !st.id || !["done", "stopped", "failed"].includes(st.status)) {
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
          // a download of a list from before another search is not this list's
          const mine = busy || !st.find || String(st.find) === String(findId);
          foundBox.classList.toggle("vrun-other", !!st.id && !mine);
          foundBox.classList.toggle("downloading", busy);
          stopButton.hidden = !busy;
          showMeter(busy ? st.meter : null);
          runStates = mine ? Object.assign({}, st.states || {}) : {};
          paintRun();
          showResult(st, busy);
          openLink.hidden = busy || !st.series_url;
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
      try {
        await postJson("/api/videos/download", { find: findId, files });
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

    actions["vid-download-stop"] = (el) => postJson("/api/videos/download/stop", {})
      .then(() => say(el, "Останавливаю…")).catch((err) => say(el, err.message, true));

    // a search or a download that goes on (or just ended) when the page opens
    fetch("/api/videos/find-state", { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => { if (st.id) { findJob.hidden = false; watchFind(); } }).catch(() => {});
    fetch("/api/videos/download-state", { credentials: "same-origin" }).then((r) => r.json())
      .then((st) => { if (st.id) { getJob.hidden = false; watchGet(); } }).catch(() => {});
  }
})();
