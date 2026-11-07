/* (v5.44.118) The phone's gallery as the computer's (the sketch E, webui/pages/phone_gallery.py): the card — «В корзину»
   (a second press within 4 s; in the trash — «Вернуть»), the rating; the sections «Брак» and «Корзина» — «Выбрать»
   (a tap chooses a tile instead of opening it), «Искать снова», «Вернуть», «Вернуть всё» with its journal and
   «Остановить».  It asks the computer's own addresses (/api/post/…, /api/parser/retry, /api/trash/…).  phone.js gives
   it ``window.LocalBooru``: ``actions`` (the taps on [data-action]), ``postJson``, ``say``. */
(function () {
  "use strict";

  var LB = window.LocalBooru;
  if (!LB || !LB.actions) { return; }
  var actions = LB.actions;
  var say = function (text) { LB.say(null, text); };

  // a button that does what cannot be taken back at once asks first: the second press within 4 s does it
  function asked(el, question) {
    var label = el.querySelector("[data-label]") || el;
    if (el.classList.contains("confirming")) {
      clearTimeout(el.confirmTimer);
      el.classList.remove("confirming");
      label.textContent = el.getAttribute("data-was") || label.textContent;
      return true;
    }
    el.setAttribute("data-was", label.textContent);
    el.classList.add("confirming");
    label.textContent = question;
    clearTimeout(el.confirmTimer);
    el.confirmTimer = setTimeout(function () {
      el.classList.remove("confirming");
      label.textContent = el.getAttribute("data-was") || label.textContent;
    }, 4000);
    return false;
  }

  function filesWord(n) {
    var d = n % 10, h = n % 100;
    if (d === 1 && h !== 11) { return n + " файл"; }
    if (d >= 2 && d <= 4 && (h < 12 || h > 14)) { return n + " файла"; }
    return n + " файлов";
  }

  // ── the card ────────────────────────────────────────────────────────────────────────────────────────────────
  function trashState(el, trashed) {
    var label = el.querySelector("[data-label]");
    el.setAttribute("data-action", trashed ? "m-restore-card" : "m-trash");
    el.classList.toggle("m-act-trash", !trashed);
    if (label) { label.textContent = trashed ? "Вернуть" : "В корзину"; }
  }

  actions["m-trash"] = function (el) {
    if (!asked(el, el.getAttribute("data-ask") || "Точно в корзину? Нажмите ещё раз")) { return; }
    // a video plays from the file: the browser lets it go first (a file being sent is not moved on Windows)
    var videos = Array.prototype.slice.call(document.querySelectorAll("video[src]"));
    videos.forEach(function (video) {
      video.setAttribute("data-src", video.getAttribute("src"));
      video.pause();
      video.removeAttribute("src");
      video.load();
    });
    el.disabled = true;
    LB.postJson("/api/post/" + el.getAttribute("data-id") + "/trash", {}).then(function () {
      trashState(el, true);
      say("Перемещено в корзину — оттуда можно вернуть");
    }, function (err) {
      videos.forEach(function (video) { video.setAttribute("src", video.getAttribute("data-src")); });
      say(err.message);
    }).then(function () { el.disabled = false; });
  };

  actions["m-restore-card"] = function (el) {
    el.disabled = true;
    LB.postJson("/api/post/" + el.getAttribute("data-id") + "/restore", {}).then(function () {
      trashState(el, false);
      say("Возвращено из корзины");
    }, function (err) { say(err.message); }).then(function () { el.disabled = false; });
  };

  actions["m-rate"] = function (el) {
    var box = el.closest(".m-rate");
    var now = Number(box.getAttribute("data-rating") || 0);
    var asked_ = Number(el.getAttribute("data-value") || 0);
    var value = asked_ === now ? 0 : asked_;
    LB.postJson("/api/post/" + el.getAttribute("data-id") + "/rating", {value: value}).then(function (data) {
      var got = Number(data.rating || 0);
      box.setAttribute("data-rating", String(got));
      box.querySelectorAll("[data-value]").forEach(function (star) {
        var n = Number(star.getAttribute("data-value"));
        star.classList.toggle("on", n <= got);
        star.setAttribute("aria-pressed", n === got ? "true" : "false");
      });
      say(got ? "Оценка: " + got : "Оценка снята");
    }, function (err) { say(err.message); });
  };

  // ── «Брак» and «Корзина»: choosing tiles ───────────────────────────────────────────────────────────────────
  var bar = document.querySelector("[data-sel-bar]");
  var actbar = document.querySelector("[data-actbar]");
  var picked = [];

  function refresh() {
    if (!actbar) { return; }
    document.querySelectorAll(".m-grid .m-tile").forEach(function (tile) {
      var on = picked.indexOf(tile.getAttribute("data-id")) >= 0;
      tile.classList.toggle("m-picked", on);
      tile.setAttribute("aria-selected", on ? "true" : "false");
    });
    actbar.querySelector("[data-sel-count]").textContent = picked.length + " выбрано";
    var go = actbar.querySelector("[data-sel-go]");
    go.disabled = !picked.length;
    go.textContent = go.getAttribute("data-go-label") + (picked.length ? " (" + picked.length + ")" : "");
  }

  function choosing(on) {
    if (!bar || !actbar) { return; }
    document.body.classList.toggle("m-choosing", on);
    var button = bar.querySelector('[data-action="m-select"]');
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = on ? "Готово" : "Выбрать";
    actbar.hidden = !on;
    if (!on) { picked = []; }
    refresh();
  }

  if (bar && actbar) {
    // while choosing, a tap on a tile chooses it instead of opening it (before phone.js hears it)
    document.addEventListener("click", function (event) {
      if (!document.body.classList.contains("m-choosing")) { return; }
      var tile = event.target.closest(".m-grid .m-tile");
      if (!tile) { return; }
      event.preventDefault();
      event.stopPropagation();
      var id = tile.getAttribute("data-id");
      var at = picked.indexOf(id);
      if (at >= 0) { picked.splice(at, 1); } else { picked.push(id); }
      refresh();
    }, true);
  }

  actions["m-select"] = function () { choosing(!document.body.classList.contains("m-choosing")); };
  actions["m-select-none"] = function () { picked = []; refresh(); };

  actions["m-retry"] = function (el) {
    if (!picked.length) { return; }
    el.disabled = true;
    LB.postJson("/api/parser/retry", {ids: picked.map(Number)}).then(function (data) {
      say((data.message || "Ищу снова") + " — ход виден на компьютере, «Парсер»");
      choosing(false);
    }, function (err) { say(err.message); el.disabled = false; });
  };

  actions["m-retry-all"] = function (el) {
    if (!asked(el, el.getAttribute("data-ask") || "Искать снова весь Брак? Нажмите ещё раз")) { return; }
    el.disabled = true;
    LB.postJson("/api/parser/retry", {all: true, tags: el.getAttribute("data-tags") || "",
                                      source: el.getAttribute("data-source") || "all"}).then(function (data) {
      say((data.message || "Ищу снова") + " — ход виден на компьютере, «Парсер»");
    }, function (err) { say(err.message); }).then(function () { el.disabled = false; });
  };

  actions["m-restore"] = function (el) {
    if (!picked.length) { return; }
    el.disabled = true;
    LB.postJson("/api/trash/restore", {ids: picked.map(Number)}).then(function (data) {
      say("Возвращено: " + filesWord(Number(data.restored || 0))
          + (data.merged ? ", слито с живыми копиями: " + data.merged : ""));
      setTimeout(function () { location.reload(); }, 900);
    }, function (err) { say(err.message); el.disabled = false; });
  };

  // ── «Вернуть всё»: a task of the computer; its journal here, «Остановить», the page anew at its end ───────────
  var job = document.querySelector("[data-job]");
  var after = 0;
  var shown = 0;
  var timer = 0;
  var seenGoing = false;
  var WORDS = {starting: "запускается", running: "идёт", stopping: "останавливается", done: "готово",
               stopped: "остановлено", failed: "прервано ошибкой"};
  var going = function (status) { return ["starting", "running", "stopping"].indexOf(status) >= 0; };

  function poll(fresh) {
    if (!job) { return; }
    clearTimeout(timer);
    fetch("/api/trash/restore-all/state?after=" + after, {credentials: "same-origin"})
      .then(function (response) { return response.json(); })
      .then(function (st) {
        var busy = going(st.status);
        if (!st.id || (!busy && !seenGoing && !fresh && !(st.ended_ago < st.show_ended))) {
          job.hidden = true;
          return;
        }
        if (st.id !== shown) { shown = st.id; job.querySelector("[data-job-log]").textContent = ""; }
        job.hidden = false;
        job.querySelector("[data-job-title]").textContent = st.label || "";
        job.querySelector("[data-job-status]").textContent = WORDS[st.status] || st.status || "";
        var log = job.querySelector("[data-job-log]");
        var lines = (st.log || []).filter(function (line) { return line.seq > after; });
        if (lines.length) {
          log.textContent += (log.textContent ? "\n" : "") + lines.map(function (line) { return line.text; }).join("\n");
          log.scrollTop = log.scrollHeight;
        }
        after = Math.max(after, st.last_seq || 0);
        job.querySelector('[data-action="m-restore-stop"]').hidden = !busy;
        document.querySelectorAll('[data-action="m-restore-all"]').forEach(function (b) { b.disabled = busy; });
        if (busy) {
          seenGoing = true;
          timer = setTimeout(function () { poll(false); }, 800);
        } else if (seenGoing) {
          seenGoing = false;
          setTimeout(function () { location.reload(); }, 2500);
        }
      })
      .catch(function () { timer = setTimeout(function () { poll(fresh); }, 3000); });
  }

  actions["m-restore-all"] = function (el) {
    if (!asked(el, el.getAttribute("data-ask") || "Вернуть всё из корзины? Нажмите ещё раз")) { return; }
    el.disabled = true;
    LB.postJson("/api/trash/restore-all", {tags: el.getAttribute("data-tags") || "",
                                           source: el.getAttribute("data-source") || "all"}).then(function () {
      say("Возвращаю — журнал выше");
      after = 0;
      seenGoing = true;
      poll(true);
    }, function (err) { say(err.message); el.disabled = false; });
  };

  actions["m-restore-stop"] = function (el) {
    el.disabled = true;
    LB.postJson("/api/trash/restore-all/stop", {}).then(function () { poll(true); }, function (err) {
      say(err.message);
    }).then(function () { el.disabled = false; });
  };

  if (job) { poll(false); }

  // the section chosen is in sight among the chips (Брак and Корзина are at their far end)
  var current = document.querySelector('.m-chips [aria-current="page"]');
  if (current) {
    var chips = current.parentElement;
    var left = current.offsetLeft - (chips.clientWidth - current.offsetWidth) / 2;
    if (left > 0) { chips.scrollLeft = left; }
  }
}());
