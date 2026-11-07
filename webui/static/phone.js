/* (v5.44.65) The pages of the phone (webui/pages/phone.py, phone_gallery.py): the switch «SFW / Всё / NSFW», the
   gallery (hints while typing tags, the order, «Показать ещё» — by itself near the end), the viewer (a swipe or the
   arrows to the neighbours, «В избранное», the сборка, a video the phone cannot play).  Going from picture to picture
   replaces the page in the history: «назад» — of the phone or of the page — goes to the gallery at once. */
(function () {
  "use strict";

  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function store(key, value) {
    try {
      if (value === null) { sessionStorage.removeItem(key); } else { sessionStorage.setItem(key, value); }
    } catch (e) { /* a private window: without it */ }
  }

  function stored(key) {
    try { return sessionStorage.getItem(key); } catch (e) { return null; }
  }

  function say(text) {
    var note = document.querySelector("[data-m-note]");
    if (!note) { return; }
    note.textContent = text;
    note.hidden = false;
    clearTimeout(say.timer);
    say.timer = setTimeout(function () { note.hidden = true; }, 4000);
  }

  function failed(response, data) {
    if (data && data.error) { return data.error; }
    if (response.status === 503) { return "Компьютер занят — попробуйте через минуту"; }
    return "Компьютер ответил ошибкой " + response.status;
  }

  function send(address, body) {
    return fetch(address, {
      method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) { throw new Error(failed(response, data)); }
        return data;
      });
    }, function () { throw new Error("Нет связи с компьютером"); });
  }

  // (v5.44.116) the program's page that opened this one (the program tells its addresses only to itself —
  // «Referrer-Policy: same-origin»), null — none
  function pageBefore() {
    try {
      var from = new URL(document.referrer);
      return from.origin === location.origin ? from : null;
    } catch (e) { return null; }
  }

  // (v5.44.116) the page before this one in the history is the program's own (as booru.js on the computer)
  function programBefore() {
    if (history.length < 2) { return false; }
    var nav = window.navigation;
    if (nav && typeof nav.canGoBack === "boolean") { return nav.canGoBack; }
    return !!pageBefore();
  }

  // ── (v5.44.66) the computer's scripts on the phone's pages (manhwa.js): what booru.js gives them there ────────────
  if (!window.LocalBooru) {
    var actions = {};
    window.LocalBooru = {
      actions: actions,
      postJson: function (address, body) { return send(address, body); },
      say: function (_el, text) { say(text); },
      programBefore: programBefore                    // (v5.44.116) title.js, watch.js: «назад» of the bar
    };
    document.addEventListener("click", function (event) {
      var el = event.target.closest("[data-action]");
      if (!el || !actions[el.getAttribute("data-action")]) { return; }
      event.preventDefault();
      actions[el.getAttribute("data-action")](el, event);
    });
    // (v5.44.122) «Это хентай» / «Это не хентай» of a title (phone.mark_box; the computer's API): the page anew — or,
    // hidden in the phone's mode now, its list
    actions["content-mark"] = function (el) {
      el.disabled = true;
      send("/api/content-mark", {kind: el.getAttribute("data-kind"), id: el.getAttribute("data-id"),
                                 hentai: el.getAttribute("data-value") === "hentai"}).then(function (data) {
        if (data.go) { location.replace(data.go); } else { location.reload(); }
      }, function (err) {
        el.disabled = false;
        say(err.message);
      });
    };
  }

  // the tabs of a title: a tool of one tab (the order of the chapters) only beside it
  document.addEventListener("click", function (event) {
    var tab = event.target.closest('[role="tab"][data-tab]');
    if (!tab) { return; }
    document.querySelectorAll("[data-pane-tool]").forEach(function (tool) {
      tool.hidden = tool.getAttribute("data-pane-tool") !== tab.getAttribute("data-tab");
    });
  });

  // ── (v5.44.120) ⬇ «Загрузки» of the bar: its number asked every few seconds while the page is seen (the page
  // «Загрузки» itself sets it from its own state) ───────────────────────────────────────────────────────────────────
  var badge = document.querySelector(".m-top [data-dl-badge]");
  if (badge && !document.getElementById("downloads")) {
    var badgeTimer = 0;
    var badgeAsking = false;             // one question at a time: the page shown again while one is on its way
    var badgeTick = function () {
      clearTimeout(badgeTimer);
      if (document.hidden || badgeAsking) { return; }
      badgeAsking = true;
      fetch("/api/downloads/count", {credentials: "same-origin", cache: "no-store"}).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (data) {
        if (!data) { return; }
        var n = Number(data.count) || 0;
        document.querySelectorAll("[data-dl-n]").forEach(function (pill) {
          pill.textContent = n ? String(n) : "";
          pill.hidden = !n;
        });
      }).catch(function () { /* the computer asleep: asked again */ }).then(function () {
        badgeAsking = false;
        if (!document.hidden) { badgeTimer = setTimeout(badgeTick, 6000); }
      });
    };
    badgeTimer = setTimeout(badgeTick, 6000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { badgeTick(); } });
  }

  // ── «SFW / Всё / NSFW» ────────────────────────────────────────────────────────────────────────────────────────
  function setMode(mode) {
    send("/api/content-mode", {mode: mode, path: location.pathname + location.search}).then(function (data) {
      if (data.go) { location.replace(data.go); } else { location.reload(); }
    }, function (err) { say(err.message); });
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (target.matches("[data-mode]")) { setMode(target.value); }
    if (target.matches("[data-submit]") && target.form) { target.form.submit(); }
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-mode-set]");
    if (button) { setMode(button.getAttribute("data-mode-set")); }
  });

  // ── the gallery ─────────────────────────────────────────────────────────────────────────────────────────────
  var grid = document.querySelector("[data-grid]");
  var more = document.querySelector("[data-more]");
  var loading = false;

  function nearEnd() {
    return more && more.getBoundingClientRect().top < window.innerHeight + 900;
  }

  function loadMore() {
    if (!more || loading) { return; }
    loading = true;
    more.setAttribute("aria-busy", "true");
    more.textContent = "Загружаю…";
    var address = more.getAttribute("href");
    fetch(address + (address.indexOf("?") >= 0 ? "&" : "?") + "part=1", {credentials: "same-origin"})
      .then(function (response) {
        if (!response.ok) { throw new Error(failed(response, null)); }
        return response.json();
      })
      .then(function (data) {
        grid.insertAdjacentHTML("beforeend", data.html || "");
        loading = false;
        if (data.more) {
          more.setAttribute("href", data.more);
          more.removeAttribute("aria-busy");
          more.textContent = "Показать ещё";
          if (nearEnd()) { loadMore(); }
        } else {
          more.remove();
          more = null;
        }
      })
      .catch(function (err) {
        loading = false;
        if (more) { more.removeAttribute("aria-busy"); more.textContent = "Показать ещё"; }
        say(err instanceof TypeError || !err.message ? "Нет связи с компьютером" : err.message);
      });
  }

  if (grid && more) {
    more.addEventListener("click", function (event) { event.preventDefault(); loadMore(); });
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        if (entries.some(function (entry) { return entry.isIntersecting; })) { loadMore(); }
      }, {rootMargin: "0px 0px 900px 0px"}).observe(more);
    }
  }

  if (grid) {
    grid.addEventListener("click", function (event) {
      if (event.target.closest(".m-tile")) { store("m-grid", location.href); }
    });
    // back from a picture: its tile in sight (the last one looked at)
    window.addEventListener("pageshow", function () {
      var at = stored("m-at");
      store("m-at", null);
      var tile = at !== null ? document.getElementById("i" + at) : null;
      if (!tile) { return; }
      var box = tile.getBoundingClientRect();
      if (box.top < 0 || box.bottom > window.innerHeight) {
        tile.scrollIntoView({block: "center"});
      }
    });
  }

  // the tags while typing: the last word of the search
  var field = document.querySelector("[data-hints]");
  var hints = document.querySelector("[data-hint-list]");
  if (field && hints) {
    var timer = 0;
    var asked = "";

    var hide = function () { hints.hidden = true; hints.textContent = ""; };
    var lastWord = function () {
      var value = field.value;
      return value.slice(value.lastIndexOf(" ") + 1);
    };
    var hint = function () {
      var word = lastWord();
      var bare = word.replace(/^-/, "");
      if (bare.length < 2 || bare.indexOf(":") >= 0) { hide(); return; }
      asked = word;
      fetch("/api/autocomplete?q=" + encodeURIComponent(word), {credentials: "same-origin"})
        .then(function (response) { return response.ok ? response.json() : {items: []}; })
        .then(function (data) {
          if (asked !== lastWord()) { return; }
          var items = (data.items || []).slice(0, 8);
          hints.textContent = "";
          items.forEach(function (item) {
            var row = document.createElement("li");
            var button = document.createElement("button");
            button.type = "button";
            button.className = "m-hint m-cat-" + String(item.category || "general").replace(/[^a-z]/g, "");
            button.setAttribute("data-insert", item.insert || item.tag);
            var name = document.createElement("span");
            name.textContent = String(item.name || item.tag).replace(/_/g, " ");
            var count = document.createElement("span");
            count.className = "m-hint-count";
            count.textContent = String(item.count || "");
            button.appendChild(name);
            button.appendChild(count);
            row.appendChild(button);
            hints.appendChild(row);
          });
          hints.hidden = !items.length;
        })
        .catch(hide);
    };
    field.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(hint, 160);
    });
    field.addEventListener("blur", function () { setTimeout(hide, 200); });
    hints.addEventListener("click", function (event) {
      var button = event.target.closest("[data-insert]");
      if (!button) { return; }
      var value = field.value;
      field.value = value.slice(0, value.lastIndexOf(" ") + 1) + button.getAttribute("data-insert") + " ";
      hide();
      field.focus();
    });
  }

  // ── the viewer ──────────────────────────────────────────────────────────────────────────────────────────────
  var stage = document.querySelector("[data-stage]");
  if (stage) {
    var prev = stage.getAttribute("data-prev") || "";
    var next = stage.getAttribute("data-next") || "";
    var media = stage.querySelector(".m-media");
    var leaving = false;

    var go = function (address, toLeft) {
      if (!address || leaving) { return; }
      leaving = true;
      if (media && !reduced) {
        media.style.transition = "transform .16s ease-out, opacity .16s ease-out";
        media.style.transform = "translateX(" + (toLeft ? "-35vw" : "35vw") + ")";
        media.style.opacity = "0";
      }
      location.replace(address);
    };

    document.addEventListener("click", function (event) {
      var link = event.target.closest("[data-go]");
      if (link) {
        event.preventDefault();
        go(link.getAttribute("href"), link.classList.contains("m-next"));
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.target.closest("input, select, textarea") || event.altKey || event.ctrlKey || event.metaKey) { return; }
      if (event.key === "ArrowLeft") { go(prev, false); }
      if (event.key === "ArrowRight") { go(next, true); }
    });

    var touch = null;
    var settle = function () {
      if (!media) { return; }
      media.style.transition = "transform .2s ease-out";
      media.style.transform = "";
      setTimeout(function () { media.style.transition = ""; }, 220);
    };
    stage.addEventListener("touchstart", function (event) {
      touch = null;
      if (event.touches.length !== 1 || (window.visualViewport && window.visualViewport.scale > 1.02)) { return; }
      var point = event.touches[0];
      if (media && media.tagName === "VIDEO") {            // the video's own bar: its line of time is not a swipe
        var box = media.getBoundingClientRect();
        if (point.clientY > box.bottom - 72) { return; }
      }
      touch = {x: point.clientX, y: point.clientY, dx: 0, sideways: null};
    }, {passive: true});
    stage.addEventListener("touchmove", function (event) {
      if (!touch || leaving) { return; }
      var point = event.touches[0];
      var dx = point.clientX - touch.x;
      var dy = point.clientY - touch.y;
      if (touch.sideways === null && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
        touch.sideways = Math.abs(dx) > Math.abs(dy) * 1.2;
      }
      if (!touch.sideways) { return; }
      touch.dx = dx;
      if (media && !reduced) {
        var nowhere = (dx > 0 && !prev) || (dx < 0 && !next);
        media.style.transform = "translateX(" + (nowhere ? dx / 4 : dx) + "px)";
      }
    }, {passive: true});
    stage.addEventListener("touchend", function () {
      var was = touch;
      touch = null;
      if (!was || !was.sideways) { return; }
      var address = was.dx < 0 ? next : prev;
      if (Math.abs(was.dx) > 60 && address) { go(address, was.dx < 0); } else { settle(); }
    });
    stage.addEventListener("touchcancel", function () { touch = null; settle(); });

    var index = stage.getAttribute("data-index");
    document.addEventListener("click", function (event) {
      var back = event.target.closest("[data-back]");
      if (!back) { return; }
      if (index !== null && Number(index) >= 0) { store("m-at", index); }
      if (stored("m-grid") && history.length > 1) {      // came from the gallery: it is the page before this one
        event.preventDefault();
        history.back();
      }
    });

    var video = stage.querySelector("[data-video]");
    var problem = stage.querySelector("[data-video-problem]");
    if (video && problem) {
      var cannot = function () {
        video.hidden = true;
        problem.hidden = false;
      };
      video.addEventListener("error", cannot);
      if (video.error) { cannot(); }               // it failed before this script came (the script waits for the page)
    }
  }

  document.addEventListener("click", function (event) {
    var star = event.target.closest("[data-fav]");
    if (star) {
      var wanted = star.getAttribute("aria-pressed") !== "true";
      star.disabled = true;
      send("/api/post/" + star.getAttribute("data-fav") + "/favorite", {value: wanted ? 1 : 0}).then(function (data) {
        var on = !!data.favorite;
        star.setAttribute("aria-pressed", on ? "true" : "false");
        star.querySelector("[data-label]").textContent = on ? "В избранном" : "В избранное";
      }, function (err) { say(err.message); }).then(function () { star.disabled = false; });
    }
    var toggle = event.target.closest("[data-set-toggle]");
    if (toggle) {
      var strip = document.getElementById(toggle.getAttribute("aria-controls"));
      var open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      strip.hidden = !open;
      var current = open ? strip.querySelector("[aria-current]") : null;
      if (current) { current.scrollIntoView({inline: "center", block: "nearest"}); }
    }
  });
  // ── (v5.44.67) a list to filter as it is typed (the tags of the manga) ──────────────────────────────────────────
  document.querySelectorAll("[data-filter]").forEach(function (input) {
    var box = document.getElementById(input.getAttribute("data-filter"));
    if (!box) { return; }
    input.addEventListener("input", function () {
      var words = input.value.trim().toLowerCase().replace(/ /g, "_");
      box.querySelectorAll("[data-filter-text]").forEach(function (item) {
        item.hidden = words !== "" && item.getAttribute("data-filter-text").indexOf(words) < 0;
      });
    });
  });

  // ── (v5.44.68) the episodes of a series: its groups (voice-overs, seasons) as tabs ───────────────────────────────
  document.addEventListener("click", function (event) {
    var tab = event.target.closest("[data-ep-group]");
    if (!tab) { return; }
    var n = tab.getAttribute("data-ep-group");
    document.querySelectorAll("[data-ep-group]").forEach(function (other) {
      other.setAttribute("aria-selected", other === tab ? "true" : "false");
    });
    document.querySelectorAll("[data-ep-list]").forEach(function (list) {
      list.hidden = list.getAttribute("data-ep-list") !== n;
    });
  });

  // ── (v5.44.68) the player of an episode ─────────────────────────────────────────────────────────────────────────
  var vp = document.querySelector("[data-vp]");
  var vpData = document.getElementById("m-vp-data");
  if (vp && vpData) {
    var ep = JSON.parse(vpData.textContent);
    var video = vp.querySelector("[data-vp-video]");
    var box = vp.querySelector("[data-vp-box]");
    var waitBox = vp.querySelector("[data-vp-wait]");
    var waitText = vp.querySelector("[data-vp-wait-text]");
    var waitBar = vp.querySelector("[data-vp-wait-bar]");
    var problem = vp.querySelector("[data-vp-problem]");
    var problemText = vp.querySelector("[data-vp-problem-text]");
    var skipButton = vp.querySelector("[data-vp-skip]");
    var marks = vp.querySelector("[data-vp-marks]");
    var audioBox = vp.querySelector("[data-vp-audio-box]");
    var audioPick = vp.querySelector("[data-vp-audio]");
    var subsBox = vp.querySelector("[data-vp-subs-box]");
    var subsPick = vp.querySelector("[data-vp-subs]");
    var autoBox = vp.querySelector("[data-vp-autonext]");
    var startAt = 0;
    var wanted = false;               // the person asked to play: it starts as soon as the episode is ready
    var polling = 0;
    var sentAt = 0;
    var nextAsked = "";

    var keepPlace = function (ended, leaving) {
      if (!video.duration || !isFinite(video.duration)) { return; }
      var body = JSON.stringify({episode: ep.episode, position: video.currentTime, duration: video.duration,
                                 ended: !!ended});
      try {
        fetch("/api/videos/progress", {method: "POST", credentials: "same-origin", keepalive: !!leaving,
                                       headers: {"Content-Type": "application/json"}, body: body})
          .catch(function () { /* said again in a few seconds */ });
      } catch (e) { /* the page is going away */ }
    };

    var drawMarks = function () {
      marks.textContent = "";
      var length = (ep.skips && ep.skips.length) || ep.duration || video.duration || 0;
      if (!length || !isFinite(length)) { return; }
      ["opening", "ending"].forEach(function (kind) {
        var range = ep.skips && ep.skips[kind];
        if (!range) { return; }
        var mark = document.createElement("span");
        mark.className = "m-vp-mark";
        mark.style.left = (100 * range[0] / length).toFixed(2) + "%";
        mark.style.width = Math.max(0.5, 100 * (range[1] - range[0]) / length).toFixed(2) + "%";
        marks.appendChild(mark);
      });
      var place = document.createElement("span");
      place.className = "m-vp-place";
      place.setAttribute("data-vp-place", "");
      marks.appendChild(place);
    };

    var showProblem = function (text) {
      waitBox.hidden = true;
      problem.hidden = false;
      problemText.textContent = text;
    };

    var start = function (src) {
      waitBox.hidden = true;
      problem.hidden = true;
      video.src = src;
      video.load();
      if (wanted) {
        video.play().catch(function () { /* the phone wants a tap: its own play button is there */ });
      }
    };

    var poll = function () {
      clearTimeout(polling);
      fetch("/api/phone/video/state?episode=" + ep.episode + "&audio=" + ep.chosen, {credentials: "same-origin"})
        .then(function (response) { return response.json(); })
        .then(function (state) {
          if (state.state === "ready") { ep.src = state.src; start(state.src); return; }
          if (state.state === "failed") { showProblem(state.error || "Серия не собралась для телефона"); return; }
          var part = Math.round(100 * (state.part || 0));
          waitText.textContent = state.state === "waiting" ? "Серия ждёт очереди — готовится другая…"
            : "Готовлю серию для телефона… " + part + "%";
          waitBar.style.width = part + "%";
          polling = setTimeout(poll, 800);
        })
        .catch(function () {
          waitText.textContent = "Нет связи с компьютером — пробую снова…";
          polling = setTimeout(poll, 2500);
        });
    };

    var prepare = function () {
      waitBox.hidden = false;
      problem.hidden = true;
      waitText.textContent = "Готовлю серию для телефона…";
      waitBar.style.width = "0%";
      send("/api/phone/video/prepare", {episode: ep.episode, audio: ep.chosen}).then(function (state) {
        if (state.src && (state.state === "ready" || state.state === "direct")) { ep.src = state.src; start(state.src); }
        else { poll(); }
      }, function (err) { showProblem(err.message); });
    };

    var fillPicks = function () {
      audioPick.textContent = "";
      (ep.audio || []).forEach(function (track) {
        var option = document.createElement("option");
        option.value = String(track.index);
        option.textContent = track.title;
        option.selected = track.index === ep.chosen;
        audioPick.appendChild(option);
      });
      audioBox.hidden = (ep.audio || []).length < 2;
      Array.prototype.slice.call(video.querySelectorAll("track")).forEach(function (old) { old.remove(); });
      subsPick.textContent = "";
      var none = document.createElement("option");
      none.value = "";
      none.textContent = "Нет";
      subsPick.appendChild(none);
      var mine = ep.sub || stored("m-subs-" + ep.series) || "";
      (ep.subs || []).forEach(function (sub) {
        var track = document.createElement("track");
        track.kind = "subtitles";
        track.src = sub.src;
        track.label = sub.label;
        track.setAttribute("data-key", sub.key);
        video.appendChild(track);
        var option = document.createElement("option");
        option.value = sub.key;
        option.textContent = sub.label;
        option.selected = sub.key === mine;
        subsPick.appendChild(option);
      });
      subsBox.hidden = !(ep.subs || []).length;
      showSubs(mine);
    };

    var showSubs = function (key) {
      Array.prototype.forEach.call(video.textTracks, function (track, n) {
        var element = video.querySelectorAll("track")[n];
        track.mode = element && element.getAttribute("data-key") === key && key ? "showing" : "disabled";
      });
    };

    var load = function (data, play) {
      ep = data;
      wanted = !!play;
      startAt = ep.resume || 0;
      nextAsked = "";
      sentAt = 0;
      clearTimeout(polling);
      skipButton.hidden = true;
      video.removeAttribute("src");
      video.poster = ep.poster;
      vp.querySelector("[data-vp-label]").textContent = ep.label;
      vp.querySelector("[data-vp-note]").textContent = ep.note || "";
      [["prev", "[data-vp-prev]"], ["next", "[data-vp-next]"]].forEach(function (pair) {
        var link = vp.querySelector(pair[1]);
        if (!link) { return; }
        if (ep[pair[0]]) {
          link.href = "/m/videos/e/" + ep[pair[0]].episode;
          link.removeAttribute("aria-disabled");
        } else {
          link.removeAttribute("href");
          link.setAttribute("aria-disabled", "true");
        }
      });
      vp.querySelectorAll(".m-ep[data-episode]").forEach(function (button) {
        var here = button.getAttribute("data-episode") === ep.episode;
        button.classList.toggle("current", here);
        if (here) { button.setAttribute("aria-current", "true"); } else { button.removeAttribute("aria-current"); }
      });
      document.title = ep.label + " — " + ep.title;
      fillPicks();
      drawMarks();
      if (ep.error && !ep.src) { showProblem(ep.error); return; }
      if (ep.src) { start(ep.src); } else { prepare(); }
    };

    video.addEventListener("loadedmetadata", function () {
      if (startAt > 0 && startAt < video.duration - 5) { video.currentTime = startAt; }
      startAt = 0;
      drawMarks();
    });
    video.addEventListener("play", function () { wanted = true; });
    video.addEventListener("pause", function () { if (!video.ended) { keepPlace(false, false); } });
    video.addEventListener("error", function () {
      if (video.getAttribute("src")) {
        showProblem("Браузер телефона это видео не показывает. Его можно сохранить на телефон и открыть видеоплеером.");
      }
    });
    video.addEventListener("timeupdate", function () {
      var now = video.currentTime;
      if (Math.abs(now - sentAt) >= 10) { sentAt = now; keepPlace(false, false); }
      var place = marks.querySelector("[data-vp-place]");
      if (place && video.duration && isFinite(video.duration)) {
        place.style.left = (100 * now / video.duration).toFixed(2) + "%";
      }
      var skip = "";
      ["opening", "ending"].forEach(function (kind) {
        var range = ep.skips && ep.skips[kind];
        if (range && now >= range[0] && now < range[1] - 1) { skip = kind; }
      });
      skipButton.hidden = !skip;
      if (skip) {
        skipButton.textContent = skip === "opening" ? "Пропустить опенинг" : "Пропустить эндинг";
        skipButton.setAttribute("data-kind", skip);
      }
      // the next episode is made for the phone while this one plays
      if (ep.next && nextAsked !== ep.next.episode && now > 20) {
        nextAsked = ep.next.episode;
        send("/api/phone/video/prepare", {episode: ep.next.episode}).catch(function () { /* asked again by its page */ });
      }
    });
    skipButton.addEventListener("click", function () {
      var range = ep.skips && ep.skips[skipButton.getAttribute("data-kind")];
      if (range) { video.currentTime = Math.min(range[1], (video.duration || range[1]) - 0.5); }
    });
    video.addEventListener("ended", function () {
      keepPlace(true, false);
      if (!ep.next || !autoBox || !autoBox.checked) { return; }
      fetch("/api/phone/video/episode/" + ep.next.episode, {credentials: "same-origin"})
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (!data.ok) { throw new Error(data.error || "следующая серия не открылась"); }
          history.replaceState(history.state, "", data.href);
          load(data, true);
        })
        .catch(function (err) { say("Следующая серия не открылась: " + err.message); });
    });
    window.addEventListener("pagehide", function () { if (!video.paused) { keepPlace(false, true); } });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && !video.paused) { keepPlace(false, true); }
    });

    audioPick.addEventListener("change", function () {
      var title = audioPick.options[audioPick.selectedIndex].textContent;
      startAt = video.currentTime || 0;
      wanted = !video.paused;
      ep.chosen = Number(audioPick.value);
      ep.src = "";
      video.pause();
      video.removeAttribute("src");
      send("/api/videos/tracks", {episode: ep.episode, audio: title}).catch(function () { /* this play only */ });
      prepare();
    });
    subsPick.addEventListener("change", function () {
      var key = subsPick.value;
      showSubs(key);
      store("m-subs-" + ep.series, key || null);
      if (!key || key.charAt(0) === "x") {
        send("/api/videos/tracks", {episode: ep.episode, value: key || "-1"}).catch(function () { /* this phone */ });
      }
    });
    if (autoBox) {
      autoBox.addEventListener("change", function () {
        send("/api/prefs", {video_autonext: autoBox.checked ? "on" : "off"}).catch(function () { /* this page */ });
      });
    }

    // the whole screen, sideways
    vp.querySelector("[data-vp-full]").addEventListener("click", function () {
      var full = document.fullscreenElement || document.webkitFullscreenElement;
      if (full) {
        (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        return;
      }
      var ask = box.requestFullscreen || box.webkitRequestFullscreen;
      if (!ask) { return; }
      Promise.resolve(ask.call(box)).then(function () {
        if (screen.orientation && screen.orientation.lock) {
          screen.orientation.lock("landscape").catch(function () { /* a phone that does not turn by itself */ });
        }
      }).catch(function () { /* not allowed here */ });
    });
    document.addEventListener("fullscreenchange", function () {
      box.classList.toggle("m-vp-whole", !!document.fullscreenElement);
      if (!document.fullscreenElement && screen.orientation && screen.orientation.unlock) {
        try { screen.orientation.unlock(); } catch (e) { /* nothing to unlock */ }
      }
    });

    load(ep, false);
  }
  // ── (v5.44.69) the phone as an app: «Компьютер не отвечает» kept by the service worker, the install button ────────
  var offlinePage = document.body.classList.contains("m-offline-page");
  var keepLocal = function (key, value) {
    try {
      if (value === undefined) { return localStorage.getItem(key); }
      localStorage.setItem(key, value);
    } catch (e) { /* a private window */ }
    return null;
  };
  if (!offlinePage) {
    keepLocal("m-seen", String(Date.now()));
    if ("serviceWorker" in navigator && window.isSecureContext &&
        !/^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname)) {
      navigator.serviceWorker.register("/m/sw.js", {scope: "/m/"}).catch(function () { /* the pages work without it */ });
    }
  } else {
    var seen = Number(keepLocal("m-seen") || 0);
    var seenLine = document.querySelector("[data-last-seen]");
    if (seen && seenLine) {
      var when = new Date(seen);
      var today = new Date();
      var yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
      var clock = when.toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});
      var day = when.toDateString() === today.toDateString() ? "сегодня"
        : when.toDateString() === yesterday.toDateString() ? "вчера"
          : when.toLocaleDateString("ru-RU", {day: "numeric", month: "long"});
      seenLine.textContent = "Последний раз на связи — " + day + " в " + clock + ".";
      seenLine.hidden = false;
    }
    var again = document.querySelector("[data-reload]");
    if (again) {
      again.addEventListener("click", function (event) { event.preventDefault(); location.reload(); });
    }
  }
  var installButton = document.querySelector("[data-install]");
  if (installButton) {
    window.addEventListener("beforeinstallprompt", function (event) {
      event.preventDefault();
      installButton.hidden = false;
      installButton.onclick = function () {
        installButton.hidden = true;
        event.prompt();
      };
    });
  }

  // ── (v5.44.116) «назад» of the bar (the review: on a title's or a series' page it led to the start of the section —
  // the list's search and place lost): the page before of the same section (the list as it was searched, a series, a
  // title) — back in the history, the phone keeps it as it was left; else — the link.  The gallery's picture, the
  // title's page and the series' page of «Лента» go back their own ways first (their handlers: defaultPrevented).
  function sectionOf(path) {
    var found = /^\/m\/([a-z]+)/.exec(path || "");
    return found ? (found[1] === "post" ? "posts" : found[1]) : "";
  }

  document.addEventListener("click", function (event) {
    var back = event.target.closest("a[data-back]");
    if (!back || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey ||
        event.altKey || history.length < 2) { return; }
    var from = pageBefore();
    var to = new URL(back.getAttribute("href") || "/m/", location.href);
    if (from && sectionOf(from.pathname) && sectionOf(from.pathname) === sectionOf(to.pathname)) {
      event.preventDefault();
      history.back();
    }
  });
}());
