/* (v5.44.70) «Скачать по ссылке» on the phone (webui/pages/phone_get.py): a link → which of the three it is
   (/api/phone/get/what) → the computer's own «Найти» and «Скачать» of the videos (video_get), the manhwa (manhwa_get)
   and nhentai (manga_get): what was found, what to take, the downloads going with «Пауза» and «Остановить».  The
   computer's pages stay as they are — their answers are only read here.  (v5.44.74) The manhwa as the computer's page
   has it now: its chapters as tiles to tick, «Пауза» and the queue of its downloads; its state without the chapters'
   list (items=none: the card says the chapter going and the counts). */
(function () {
  "use strict";

  var box = document.querySelector("[data-get]");
  if (!box || !window.LocalBooru) { return; }
  var send = window.LocalBooru.postJson;
  var form = box.querySelector("[data-get-form]");
  var field = form.querySelector("input[name='url']");
  var findButton = form.querySelector("button[type='submit']");
  var pasteButton = form.querySelector("[data-get-paste]");
  var step = box.querySelector("[data-get-step]");
  var foundBox = box.querySelector("[data-get-found]");
  var goingBox = box.querySelector("[data-get-going]");
  var jobsBox = box.querySelector("[data-get-jobs]");
  var RUNNING = ["starting", "running", "stopping"];
  var KINDS = {
    video: {label: "Видео", find: "/api/videos/find", findState: "/api/videos/find-state", get: "/api/videos/download",
            state: "/api/videos/download-state", stop: "/api/videos/download/stop", pause: "/api/videos/download/pause",
            unqueue: "/api/videos/download/unqueue", prefix: /^Скачать с сайта:\s*/},
    manhwa: {label: "Манхва", find: "/api/manhwa/find", findState: "/api/manhwa/find-state",
             get: "/api/manhwa/download", state: "/api/manhwa/download-state?items=none",
             stop: "/api/manhwa/download/stop", pause: "/api/manhwa/download/pause",
             unqueue: "/api/manhwa/download/unqueue", prefix: /^Скачать:\s*/},
    manga: {label: "nhentai", get: "/api/manga/download", state: "/api/manga/download-state",
            stop: "/api/manga/download/stop", unqueue: "/api/manga/download/unqueue",   // (v5.44.107) a queue
            prefix: /^(?:С прошлого запуска — )?Скачать с nhentai:\s*/}
  };
  var ORDER = ["video", "manhwa", "manga"];

  // ── little things ─────────────────────────────────────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function plural(n, forms) {
    var d = n % 10, h = n % 100;
    return n + " " + (d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2]);
  }

  function megabytes(bytes) {
    var n = Number(bytes) || 0;
    if (n >= 1024 * 1024 * 1024) { return (n / 1024 / 1024 / 1024).toFixed(1).replace(".", ",") + " ГБ"; }
    return Math.max(1, Math.round(n / 1024 / 1024)) + " МБ";
  }

  function word(n, forms) {
    var d = n % 10, h = n % 100;
    return d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2];
  }

  function speedOf(bytes) {           // the strips of a chapter come by hundreds of kilobytes
    var n = Number(bytes) || 0;
    return n < 1024 * 1024 ? Math.max(1, Math.round(n / 1024)) + " КБ/с"
      : (n / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ/с";
  }

  function clockOf(seconds) {
    var m = Math.max(1, Math.round((Number(seconds) || 0) / 60));
    return m < 60 ? m + " мин" : Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "");
  }

  function natural(a, b) {
    return (parseFloat(a) - parseFloat(b)) || String(a).localeCompare(String(b), "ru");
  }

  // the computer's address of a page (/videos/s/…, /manhwa/t/…, /manga/g/…) — the phone's page of it
  function phoneLink(href) {
    return /^\/(videos|manhwa|manga)(\/|$)/.test(href || "") ? "/m" + href : href || "";
  }

  function store(key, value) {
    try { sessionStorage.setItem(key, value); } catch (e) { /* a private window: without it */ }
  }

  function stored(key) {
    try { return sessionStorage.getItem(key); } catch (e) { return null; }
  }

  function kept(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }

  function keep(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* only this time */ }
  }

  function getJson(address) {
    return fetch(address, {credentials: "same-origin", cache: "no-store"}).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || "Компьютер ответил ошибкой " + response.status);
        }
        return data;
      });
    }, function () { throw new Error("Нет связи с компьютером"); });
  }

  // a task's state with only the lines of its journal not seen yet (a long download has hundreds): a task not seen
  // before — asked without lines, then its newest line alone
  var marks = {};
  function stateOf(address) {
    var mark = marks[address] || (marks[address] = {id: null, seq: 999999999, line: ""});
    var ask = function () { return getJson(address + (address.indexOf("?") >= 0 ? "&" : "?") + "after=" + mark.seq); };
    return ask().then(function (st) {
      if (st.id && st.id !== mark.id) {          // another task: its newest line asked for alone
        mark.id = st.id;
        mark.line = "";
        mark.seq = Math.max(0, (Number(st.last_seq) || 1) - 1);
        return ask().then(function (again) { return took(mark, again); });
      }
      return took(mark, st);
    });
  }

  function took(mark, st) {
    var lines = st.log || [];
    if (lines.length) { mark.line = lines[lines.length - 1].text; }
    if (st.last_seq) { mark.seq = st.last_seq; }
    st.line = mark.line;
    return st;
  }

  // the line under the field: what goes on now, or why not
  function tell(text, bad, extra) {
    step.textContent = "";
    step.hidden = !text;
    if (!text) { return; }
    step.className = "m-get-step" + (bad ? " m-get-bad" : "");
    if (!bad) { step.append(el("span", "m-get-spin")); }
    step.append(el("span", "m-get-step-text", text));
    if (extra) { step.append(extra); }
  }

  function busy(on) {
    findButton.disabled = on;
    findButton.textContent = on ? "Ищу…" : "Найти";
  }

  // the field kept in the page's address: «назад» and a reload find it (searched again only with go=1)
  function keepField(text, again) {
    try {
      history.replaceState(history.state, "", "/m/get?" + (again ? "go=1&" : "") + "url=" + encodeURIComponent(text));
    } catch (e) { /* the address stays */ }
  }

  // ── «Найти» ───────────────────────────────────────────────────────────────────────────────────────────────────
  var finding = null;            // {kind, id, text} — a search going
  var shown = null;              // {kind, id} — what the page shows

  function ask(text) {
    text = String(text || "").trim();
    if (!text) { tell("Вставьте ссылку", true); field.focus(); return; }
    keepField(text);
    foundBox.hidden = true;
    foundBox.textContent = "";
    shown = null;
    busy(true);
    tell("Смотрю, что это за ссылка…");
    send("/api/phone/get/what", {text: text}).then(function (w) {
      if (w.kind === "manga") {
        busy(false);
        tell("");
        showManga(w.ids);
        return null;
      }
      tell(w.kind === "manhwa" ? w.site + ": ищу тайтл…" : "Читаю страницу…");
      return send(KINDS[w.kind].find, {url: w.url}).then(function (reply) {
        finding = {kind: w.kind, id: reply.id, text: text};
        store("m-get-last", JSON.stringify(finding));
        waitFound();
      });
    }).catch(function (err) {
      busy(false);
      var sfw = /SFW/.test(err.message);
      var extra = null;
      if (sfw) {
        keepField(text, true);
        extra = el("button", "m-button m-quiet", "Показать всё");
        extra.type = "button";
        extra.setAttribute("data-mode-set", "all");
      }
      tell(err.message, true, extra);
    });
  }

  function waitFound() {
    if (!finding) { return; }
    var mine = finding;
    stateOf(KINDS[mine.kind].findState).then(function (st) {
      if (finding !== mine) { return; }
      if (st.id !== mine.id || RUNNING.indexOf(st.status) >= 0) {
        if (st.id === mine.id && st.line) { tell(st.line); }
        setTimeout(waitFound, 700);
        return;
      }
      finding = null;
      busy(false);
      if (st.found) {
        tell("");
        showFound(mine.kind, st);
        return;
      }
      var why = st.failed || st.error || (st.status === "stopped" ? "Поиск остановлен" : "Не нашлось — проверьте ссылку");
      tell(why, true, mine.kind === "video" ? el("p", "m-muted", "Мангу и манхву программа берёт с Remanga, ReComics, "
        + "MangaLib, MangaBuff, ReadManga, Desu, com-x.life, MangaDex, а хентай — с nhentai, Хентай-тян, HentaiLib и "
        + "AllHentai; с других сайтов — только видео.") : null);
    }).catch(function () {
      if (finding === mine) { setTimeout(waitFound, 1500); }
    });
  }

  function showFound(kind, st) {
    foundBox.textContent = "";
    shown = {kind: kind, id: st.id};
    if (kind === "video") { showVideo(st); } else { showManhwa(st); }
    foundBox.hidden = false;
  }

  // the card of what was found: its cover, its names, its facts
  function hero(cover, title, alt, facts) {
    var head = el("div", "m-hero m-get-hero");
    var side = el("div", "m-hero-cover");
    var frame = el("span", "m-cover");
    var initial = el("span", "m-get-initial", String(title || "?").trim().charAt(0).toUpperCase());
    frame.append(initial);
    if (cover) {
      var img = el("img");
      img.alt = "";
      img.onload = function () { initial.hidden = true; };
      img.onerror = function () { img.remove(); };
      img.src = cover;
      frame.append(img);
    }
    side.append(frame);
    var about = el("div", "m-hero-about");
    about.append(el("h2", "m-get-title", title || "Без названия"));
    if (alt) { about.append(el("p", "m-alt", alt)); }
    var chips = el("p", "m-facts");
    facts.filter(Boolean).forEach(function (x) { chips.append(el("span", "", String(x))); });
    if (chips.children.length) { about.append(chips); }
    head.append(side, about);
    return {head: head, about: about};
  }

  // the button at the bottom of the screen: what «Скачать» takes, where it goes
  function goBar() {
    var bar = el("div", "m-get-bar");
    var go = el("button", "m-button m-get-go");
    go.type = "button";
    var sum = el("p", "m-get-sum");
    bar.append(go, sum);
    return {node: bar, go: go, sum: sum};
  }

  function check(label, on, name) {
    var row = el("label", "m-check");
    var tick = el("input");
    tick.type = "checkbox";
    tick.checked = !!on;
    if (name) { tick.name = name; }
    row.append(tick, el("span", "", label));
    return {node: row, tick: tick};
  }

  function quick(picks, onPick) {
    var tools = el("span", "m-get-quick");
    picks.forEach(function (pair) {
      var b = el("button", "m-get-link", pair[1]);
      b.type = "button";
      b.addEventListener("click", function () { onPick(pair[0]); });
      tools.append(b);
    });
    return tools;
  }

  // ── the videos ────────────────────────────────────────────────────────────────────────────────────────────────
  var HAVE = {whole: "есть", part: "докачается", other: "свой файл"};

  function showVideo(st) {
    var f = st.found;
    var findId = st.id;
    var facts = f.facts || {};
    var view = hero(f.poster ? "/api/videos/find-poster?find=" + encodeURIComponent(findId) : "", f.title,
      (facts.alt || []).join(" / "), [facts.year, facts.type, facts.episodes ? "серий: " + facts.episodes : ""]);
    var folder = String(f.folder || "").split(/[\\/]/).filter(Boolean).pop() || String(f.folder || "");
    view.about.append(el("p", "m-get-where", "Папка «" + folder + "» — "
      + (f.new_folder ? "новая" : "уже есть, новые серии лягут в неё")));
    (f.sources || []).forEach(function (x) {
      view.about.append(el("p", "m-get-note" + (x.exact ? "" : " m-get-warn"),
        (x.dub || "Серии — с " + x.site) + ": " + x.what + " — " + x.how));
    });
    // (v5.44.106) a series of YummyAnime, (v5.44.109) of Hentasis: watched without downloading (/api/watch/can)
    fetch("/api/watch/can?phone=1&url=" + encodeURIComponent(f.page_url || ""), { credentials: "same-origin" })
      .then(function (r) { return r.json(); }).then(function (can) {
        if (!can || !can.watch || view.about.querySelector(".m-watch-link")) return;
        var watching = el("a", "m-button m-quiet m-watch-link", "Смотреть без скачивания");
        watching.href = can.href;
        view.about.append(watching);
      }).catch(function () { /* no «Смотреть» — «Скачать» is there */ });
    foundBox.append(view.head);
    if (facts.about) {
      var more = el("details", "m-more-text m-get-about");
      more.append(el("summary", "", "Описание"), el("p", "m-get-about-text", facts.about));
      foundBox.append(more);
    }
    var pick = f.choose === "dubs" ? dubsPicker(f, count) : filesPicker(f, count);
    foundBox.append(pick.node);
    var bar = goBar();
    foundBox.append(bar.node);

    function count() {
      var files = pick.chosen();
      var video = jobs.video;
      var other = !!(video && RUNNING.indexOf(video.status) >= 0 && String(video.find) !== String(findId));
      var said = f.choose === "dubs" || files.some(function (i) { return f.files[i].kind !== "video"; })
        ? plural(files.length, ["файл", "файла", "файлов"]) : plural(files.length, ["серию", "серии", "серий"]);
      bar.go.disabled = !files.length;
      bar.go.textContent = files.length ? (other ? "В очередь: " : "Скачать ") + said : pick.empty();
      var bytes = 0, streams = false;
      files.forEach(function (i) { bytes += Number(f.files[i].size) || 0; streams = streams || !!f.files[i].stream; });
      bar.sum.textContent = (bytes ? (streams ? "≈ " : "") + megabytes(bytes) + ", " : "") + "в папку «" + folder + "»"
        + (other ? " — после текущего скачивания" : "");
    }
    count();
    bar.go.addEventListener("click", function () {
      var files = pick.chosen();
      if (!files.length) { return; }
      bar.go.disabled = true;
      send(KINDS.video.get, {find: findId, files: files, quality: pick.quality(), merge: pick.merge()})
        .then(function (reply) {
          window.LocalBooru.say(null, reply.queued ? (reply.queued > 1 ? "В очереди " + reply.queued + "-м" :
            "В очереди следующим") + ": начнётся само" : "Скачивание началось");
          watch("video", true);
          goingBox.scrollIntoView({block: "start", behavior: "smooth"});
        }).catch(function (err) { window.LocalBooru.say(null, err.message); })
        .then(function () { bar.go.disabled = false; count(); });
    });
    shownCount = count;
  }

  // the episodes of a page as tiles: the new ones ticked
  function filesPicker(f, changed) {
    var node = el("div", "m-get-pick");
    var groups = {}, order = [];
    f.files.forEach(function (x) {
      var key = x.kind === "audio" ? "\u0000" : x.group || "";
      if (!groups[key]) { groups[key] = []; order.push(key); }
      groups[key].push(x);
    });
    order.sort(function (a, b) { return (a === "\u0000") - (b === "\u0000"); });
    var head = el("div", "m-count m-get-head");
    head.append(el("h3", "m-count-title", "Серии"), quick([["new", "новые"], ["all", "все"], ["none", "ничего"]],
      function (how) {
        boxes().forEach(function (tick) {
          if (tick.disabled) { return; }
          tick.checked = how === "all" || (how === "new" && tick.dataset.kind === "video");
          paintTile(tick);
        });
        changed();
      }));
    node.append(head);
    order.forEach(function (key) {
      if (order.length > 1 || key) { node.append(el("h4", "m-get-group", key === "\u0000" ? "Звук" : key || "Серии")); }
      var grid = el("div", "m-ep-grid m-get-eps");
      groups[key].forEach(function (x) { grid.append(tile(x)); });
      node.append(grid);
    });
    node.addEventListener("change", function (event) {
      if (event.target.matches("input[type='checkbox']")) { paintTile(event.target); changed(); }
    });

    function tile(x) {
      var label = el("label", "m-ep m-get-ep" + (x.kind === "audio" ? " m-get-audio" : ""));
      var tick = el("input", "m-sr");
      tick.type = "checkbox";
      tick.value = String(x.i);
      tick.dataset.kind = x.kind;
      tick.disabled = x.have === "whole" || x.have === "other" || !!x.blocked;
      tick.checked = !tick.disabled && x.kind === "video";
      label.dataset.i = String(x.i);
      if (x.have) { label.dataset.have = x.have; }
      var mark = x.blocked && !x.have ? "нет" : HAVE[x.have] || (x.quality || "");
      label.append(tick, el("span", "m-get-ep-n", x.kind === "audio" ? "♪" : x.label || "•"),
        el("span", "m-get-ep-mark", mark));
      label.title = [x.title, x.note, x.blocked ? "Не скачать: " + x.blocked : ""].filter(Boolean).join("\n");
      tick.setAttribute("aria-label", (x.kind === "audio" ? "Звук " + (x.title || x.name) : "Серия " + (x.label || ""))
        + (mark ? ", " + mark : ""));
      paintTile(tick);
      return label;
    }

    function boxes() { return Array.prototype.slice.call(node.querySelectorAll("input[type='checkbox']")); }

    return {
      node: node,
      chosen: function () { return boxes().filter(function (t) { return t.checked; }).map(function (t) { return Number(t.value); }); },
      empty: function () { return boxes().some(function (t) { return !t.disabled; }) ? "Отметьте серии" : "Всё уже скачано"; },
      quality: function () { return ""; },
      merge: function () { return false; }
    };
  }

  function paintTile(tick) {
    var tile = tick.closest(".m-get-ep");
    if (tile) { tile.classList.toggle("on", tick.checked); tile.classList.toggle("off", tick.disabled); }
  }

  // every voice-over of a title: the kind («Дубляж», «Озвучка», «Субтитры»), its studios, the quality — and the
  // episodes; what is downloaded is each episode ticked of each studio ticked, not what is there already
  var KIND_NAMES = [["dub", "Дубляж"], ["voice", "Озвучка"], ["subs", "Субтитры"]];
  function kindOf(name) {
    return /^дубляж/i.test(name || "") ? "dub" : /^субтитры/i.test(name || "") ? "subs" : "voice";
  }

  function dubsPicker(f, changed) {
    var node = el("div", "m-get-pick m-get-dubs");
    var groups = {}, names = [];
    f.files.forEach(function (x) {
      var name = x.group || "";
      if (!groups[name]) { groups[name] = []; names.push(name); }
      groups[name].push(x);
    });
    var chosen = {};               // studio -> true
    var episodes = {};             // label -> true
    names.forEach(function (name) {
      if (groups[name].some(function (x) { return x.have === "whole" || x.have === "part"; })) { chosen[name] = true; }
    });
    var present = KIND_NAMES.filter(function (k) { return names.some(function (n) { return kindOf(n) === k[0]; }); });
    var begun = names.filter(function (n) { return chosen[n]; })[0];
    var kind = begun !== undefined ? kindOf(begun) : (present.filter(function (k) { return k[0] === "voice"; })[0]
      || present[0] || ["voice"])[0];

    node.append(el("h3", "m-get-h", "Что скачать"));
    var tabs = el("div", "m-tabs-line m-get-kinds-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Вид");
    present.forEach(function (k) {
      var b = el("button");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.dataset.kind = k[0];
      b.append(document.createTextNode(k[1] + " "), el("span", "", ""));
      b.addEventListener("click", function () { kind = k[0]; paint(); });
      tabs.append(b);
    });
    node.append(tabs);
    var studios = el("ul", "m-get-studios");
    node.append(studios);
    var subsNote = el("p", "m-get-note m-get-pad", "Слова субтитров вшиты в картинку: каждый релиз — отдельное видео, "
      + "весит как серия целиком и в общий файл серии не собирается.");
    node.append(subsNote);
    var row = el("div", "m-get-row");
    var qualityBox = el("label", "m-pick");
    var quality = el("select");
    [["best", "лучшее"], ["1080", "не выше 1080p"], ["720", "не выше 720p"], ["480", "не выше 480p"],
      ["360", "не выше 360p"]].forEach(function (q) {
      var option = el("option", "", q[1]);
      option.value = q[0];
      quality.append(option);
    });
    quality.value = kept("m-get-quality", "best");
    if (!quality.value) { quality.value = "best"; }
    quality.addEventListener("change", function () { keep("m-get-quality", quality.value); });
    qualityBox.append(el("span", "", "Качество"), quality);
    var merge = check("Собрать озвучки серии в один файл", box.dataset.merge === "1", "merge");
    row.append(qualityBox, merge.node);
    node.append(row);
    var head = el("div", "m-count m-get-head");
    var counted = el("span", "m-get-counted");
    head.append(el("h3", "m-count-title", "Серии"), counted, quick([["all", "все"], ["none", "ничего"]], function (how) {
      grid.querySelectorAll("input").forEach(function (tick) {
        if (!tick.disabled) { tick.checked = how === "all"; }
        if (tick.checked) { episodes[tick.value] = true; } else { delete episodes[tick.value]; }
      });
      paint();
    }));
    node.append(head);
    var labels = [];
    f.files.forEach(function (x) { if (labels.indexOf(x.label || "") < 0) { labels.push(x.label || ""); } });
    labels.sort(natural);
    var grid = el("div", "m-ep-grid m-get-eps");
    labels.forEach(function (label) {
      var tile = el("label", "m-ep m-get-ep");
      tile.dataset.label = label;
      var tick = el("input", "m-sr");
      tick.type = "checkbox";
      tick.value = label;
      tile.append(tick, el("span", "m-get-ep-n", label || "•"), el("span", "m-get-ep-mark", ""));
      grid.append(tile);
    });
    node.append(grid);
    node.addEventListener("change", function (event) {
      var tick = event.target;
      if (tick.closest(".m-get-studios")) {
        if (tick.checked) { chosen[tick.value] = true; } else { delete chosen[tick.value]; }
      } else if (tick.closest(".m-get-eps")) {
        if (tick.checked) { episodes[tick.value] = true; } else { delete episodes[tick.value]; }
      } else {
        return;
      }
      paint();
    });

    function shortName(name) { return String(name || "").replace(/^(озвучка|дубляж|субтитры)\s+/i, "") || name || "—"; }

    function facts(list) {
      var best = 0, players = [];
      list.forEach(function (x) {
        best = Math.max(best, parseInt(String(x.quality || "").replace(/\D+/g, ""), 10) || 0);
        String(x.note || "").replace("Плеер: ", "").split(", ").forEach(function (p) {
          if (p && players.indexOf(p) < 0) { players.push(p); }
        });
      });
      return [best ? "до " + best + "p" : "", players.join(", ")].filter(Boolean).join(" · ");
    }

    function paint() {
      tabs.querySelectorAll("[role='tab']").forEach(function (b) {
        var mine = names.filter(function (n) { return kindOf(n) === b.dataset.kind; });
        var on = mine.filter(function (n) { return chosen[n]; }).length;
        b.setAttribute("aria-selected", b.dataset.kind === kind ? "true" : "false");
        b.querySelector("span").textContent = on ? on + " из " + mine.length : String(mine.length);
      });
      studios.textContent = "";
      names.filter(function (n) { return kindOf(n) === kind; }).forEach(function (name) {
        var list = groups[name];
        var had = list.filter(function (x) { return x.have === "whole"; }).length;
        var li = el("li");
        var row = el("label", "m-check m-get-studio");
        var tick = el("input");
        tick.type = "checkbox";
        tick.value = name;
        tick.checked = !!chosen[name];
        var text = el("span", "m-get-studio-text");
        text.append(el("b", "", shortName(name)), el("span", "m-get-studio-meta", [facts(list),
          (had ? "есть " + had + " из " : "") + plural(list.length, ["серия", "серии", "серий"])].filter(Boolean).join(" · ")));
        row.append(tick, text);
        row.title = name;
        li.append(row);
        studios.append(li);
      });
      subsNote.hidden = kind !== "subs";
      var some = Object.keys(chosen).length > 0;
      var ticked = 0;
      grid.querySelectorAll(".m-get-ep").forEach(function (tile) {
        var label = tile.dataset.label;
        var files = f.files.filter(function (x) { return (x.label || "") === label && chosen[x.group || ""]; });
        var had = files.filter(function (x) { return x.have === "whole" || x.have === "other"; }).length;
        var tick = tile.querySelector("input");
        tick.disabled = !files.length || had === files.length;
        tick.checked = !tick.disabled && !!episodes[label];
        if (tick.checked) { ticked += 1; }
        tile.dataset.have = files.length && had === files.length ? "whole" : had ? "part" : "";
        tile.querySelector(".m-get-ep-mark").textContent = !some ? "" : !files.length ? "нет" : had === files.length
          ? "есть" : had ? had + "/" + files.length : "";
        paintTile(tick);
      });
      counted.textContent = some ? "отмечено " + ticked + " из " + labels.length : "";
      changed();
    }

    function picked() {
      return f.files.filter(function (x) {
        return episodes[x.label || ""] && chosen[x.group || ""] && x.have !== "whole" && x.have !== "other" && !x.blocked;
      }).map(function (x) { return x.i; });
    }
    setTimeout(paint, 0);

    return {
      node: node,
      chosen: picked,
      empty: function () { return Object.keys(chosen).length ? "Отметьте серии" : "Выберите озвучку"; },
      quality: function () { return quality.value; },
      merge: function () { return merge.tick.checked; }
    };
  }

  // ── the manhwa ────────────────────────────────────────────────────────────────────────────────────────────────
  function showManhwa(st) {
    var f = st.found;
    var findId = st.id;
    var view = hero(f.cover ? "/api/manhwa/find-cover?find=" + encodeURIComponent(findId) : "", f.title,
      (f.alt || []).join(" · "), [f.site_label, f.type, f.year, f.status, f.age]);
    if (f.cover_hidden) { view.about.append(el("p", "m-muted", "Обложка скрыта (SFW)")); }
    foundBox.append(view.head);
    // (v5.44.72) the card's own words: a link of a site that lets no program in, a list of chapters not given
    (f.notes || []).forEach(function (note) { foundBox.append(el("p", "m-get-note m-get-warn m-get-said", note)); });
    if (f.about) {
      var more = el("details", "m-more-text m-get-about");
      more.append(el("summary", "", "Описание"), el("p", "m-get-about-text", f.about));
      foundBox.append(more);
    }
    foundBox.append(readBlock(f));                    // (v5.44.82) read it without downloading
    var opts = el("div", "m-get-opts");
    var branches = f.branches || [];
    if (branches.length > 1) {
      var pickBox = el("label", "m-pick");
      var select = el("select");
      branches.forEach(function (b) {
        var option = el("option", "", (b.name || "без названия") + " — " + plural(b.count, ["глава", "главы", "глав"])
          + (b.votes ? ", " + plural(b.votes, ["оценка", "оценки", "оценок"]) : ""));
        option.value = b.id;
        option.selected = b.id === f.branch;
        select.append(option);
      });
      select.addEventListener("change", function () {
        busy(true);
        tell("Читаю главы этих переводчиков…");
        send(KINDS.manhwa.find, {url: f.url, branch: select.value}).then(function (reply) {
          finding = {kind: "manhwa", id: reply.id, text: field.value};
          store("m-get-last", JSON.stringify(finding));
          waitFound();
        }).catch(function (err) { busy(false); tell(err.message, true); });
      });
      pickBox.append(el("span", "", "Переводчики"), select);
      opts.append(pickBox);
    } else if (branches.length === 1 && branches[0].name) {
      opts.append(el("p", "m-get-note", "Переводчики: " + branches[0].name));
    }
    var where = el("fieldset", "m-get-where-pick");
    where.append(el("legend", "", "Куда"));
    [["manhwa", "Манхва — лентой"], ["manga", "Манга — по страницам"]].forEach(function (pair) {
      var row = el("label", "m-check");
      var radio = el("input");
      radio.type = "radio";
      radio.name = "m-get-where";
      radio.value = pair[0];
      radio.checked = f.kind === pair[0];
      radio.disabled = pair[0] === "manga" && !(f.roots && f.roots.manga);
      row.append(radio, el("span", "", pair[1] + (radio.disabled ? " (папка манги не выбрана)" : "")));
      where.append(row);
    });
    opts.append(where);
    var follow = check("Новые главы — скачивать самой (раз в сутки, ночью)", true, "follow");
    opts.append(follow.node);
    var giving = (f.helpers || []).filter(function (h) { return h.gives > 0; });
    var helpers = null;
    if (giving.length) {
      helpers = check("Главы, которых нет бесплатно на " + f.site_label + ", — брать с других сайтов",
        f.helpers_on !== false, "helpers");
      opts.append(helpers.node);
      var list = el("ul", "m-get-helpers");
      giving.forEach(function (h) {
        list.append(el("li", "", h.site_label + " — «" + h.title + "»: " + plural(h.gives, ["глава", "главы", "глав"])
          + (h.first ? " (" + h.first + (h.last !== h.first ? " — " + h.last : "") + ")" : "")));
      });
      opts.append(list);
    } else if ((f.helpers || []).length) {
      opts.append(el("p", "m-get-note", "Тайтл есть и на " + f.helpers.map(function (h) { return h.site_label; })
        .join(", ") + " — новых глав оттуда сейчас нет"));
    }
    var counts = el("p", "m-get-note");
    var paid = el("p", "m-get-note m-get-warn");
    opts.append(counts, paid);
    foundBox.append(opts);
    var others = function () { return helpers ? helpers.tick.checked : f.helpers_on !== false; };
    var pick = chaptersPicker(f, others, update);       // (v5.44.74) the chapters as tiles to tick, by tomes
    foundBox.append(pick.node);
    if (helpers) {
      helpers.tick.addEventListener("change", function () { pick.repaint(helpers.tick.checked ? "on" : "off"); update(); });
    }
    var bar = goBar();
    foundBox.append(bar.node);

    function update() {
      var on = !!(helpers && helpers.tick.checked);
      var chosen = pick.chosen().length, open = pick.open(), waiting = pick.waiting();
      var job = jobs.manhwa;
      var other = !!(job && RUNNING.indexOf(job.status) >= 0 && String(job.find) !== String(findId));
      counts.textContent = "Глав: " + f.chapters + (f.first ? " (" + f.first + " — " + f.last + ")" : "")
        + (f.have ? " · уже в папке: " + f.have : "");
      var left = on ? f.paid_left : f.paid_count;
      var covered = on ? f.paid_count - f.paid_left : 0;
      var soon = function (items) {
        return (items || []).slice(0, 3).map(function (c) { return c.label + (c.free_at ? " — " + c.free_at : ""); })
          .join("; ");
      };
      // (v5.44.73) a paid chapter with no date to become free (Remanga's licensed ones): it waits for another site
      var dated = function (items) { return (items || []).some(function (c) { return c.free_at; }); };
      var nowhere = "бесплатно их пока нет ни на одном из сайтов — появятся, программа скачает сама.";
      var words = "";
      if (f.paid_count && covered && !left) {
        words = "Платных на " + f.site_label + ": " + f.paid_count + " — все есть бесплатно на других сайтах.";
      } else if (f.paid_count && covered) {
        words = "Платных: " + f.paid_count + ", из них " + covered + " есть бесплатно на других сайтах; " + (dated(f.waiting)
          ? "остальные программа возьмёт, когда сайт сделает их бесплатными" + (soon(f.waiting) ? ": "
          + soon(f.waiting) : "") + "." : "остальные " + left + " — " + nowhere);
      } else if (f.paid_count) {
        words = "Платных: " + f.paid_count + " — " + (dated(f.paid) ? "их программа не скачивает, возьмёт сама, когда "
          + "сайт сделает их бесплатными" + (soon(f.paid) ? ": " + soon(f.paid) : "") + "." : nowhere);
      }
      paid.textContent = words;
      paid.hidden = !words;
      var waits = !chosen && !open && waiting;    // nothing to take now: the title kept, its paid ones awaited
      bar.go.textContent = chosen ? (other ? "В очередь: " : "Скачать ") + plural(chosen, ["главу", "главы", "глав"])
        : open ? "Отметьте главы" : waits ? "Ждать платные" : "Всё уже скачано";
      bar.go.disabled = !chosen && !waits;
      bar.sum.textContent = (f.folder ? "в папку «" + String(f.folder).split(/[\\/]/).filter(Boolean).pop() + "»" : "")
        + (other && chosen ? " — после текущего скачивания" : "");
      pick.counted.textContent = open ? "отмечено " + chosen + " из " + open : "";
    }
    update();
    bar.go.addEventListener("click", function () {
      var whereTick = where.querySelector("input:checked");
      bar.go.disabled = true;
      send(KINDS.manhwa.get, {find: findId, where: whereTick ? whereTick.value : "", follow: follow.tick.checked,
        helpers: others(), chapters: pick.chosen()}).then(function (reply) {
        window.LocalBooru.say(null, reply.queued ? (reply.queued > 1 ? "В очереди " + reply.queued + "-м" :
          "В очереди следующим") + ": начнётся само" : "Скачивание началось");
        watch("manhwa", true);
        goingBox.scrollIntoView({block: "start", behavior: "smooth"});
      }).catch(function (err) { window.LocalBooru.say(null, err.message); }).then(function () { update(); });
    });
    shownCount = update;
  }

  // (v5.44.74) the chapters of a title as tiles, by tomes: the ones some site has free ticked; the ones in the folder
  // «есть», the paid ones nobody has free «платная», another site's — «с MangaLib» (or not, without that box)
  // (v5.44.82) «Читать с начала», «Продолжить: Глава 12» and every chapter — read without downloading it, in the
  // program's reader (webui/pages/manga_online.py): the chapter's place in the card's list
  function readBlock(f) {
    var href = function (n, extra) {
      return "/m/online?url=" + encodeURIComponent(f.url) + (f.branch ? "&branch=" + encodeURIComponent(f.branch) : "")
        + "&ch=" + n + (extra || "");
    };
    var box = el("div", "m-get-read");
    var list = f.list || [];
    var first = -1;
    list.some(function (c, i) { if (!c.wait) { first = i; return true; } return false; });
    if (first < 0) { return box; }
    var row = el("div", "m-get-read-row");
    var start = el("a", "m-button m-quiet", "Читать с начала");
    start.href = href(first + 1, "&from=start");
    row.append(start);
    box.append(row);
    fetch("/api/online/last?url=" + encodeURIComponent(f.url), {credentials: "same-origin"})
      .then(function (r) { return r.json(); }).then(function (reply) {
        var last = reply && reply.last;
        if (!last || !last.ch) { return; }
        var n = last.done && last.ch < list.length ? last.ch + 1 : last.ch;
        var go = el("a", "m-button", "Продолжить: " + ((list[n - 1] && list[n - 1].label) || last.label));
        go.href = href(n);
        row.prepend(go);
      }).catch(function () { /* nothing kept: «Читать с начала» only */ });
    var all = el("details", "m-more-text m-get-read-all");
    all.append(el("summary", "", "Читать главу без скачивания"));
    var ol = el("ol", "m-get-read-list");
    for (var i = list.length - 1; i >= 0; i--) {
      var c = list[i];
      var li = el("li");
      if (c.wait) {
        li.append(el("span", "m-muted", c.label + " — платная"));
      } else {
        var a = el("a", "", c.label + (c.name ? " — " + c.name : ""));
        a.href = href(i + 1);
        li.append(a);
      }
      ol.append(li);
    }
    all.append(ol);
    box.append(all);
    return box;
  }

  function chaptersPicker(f, others, changed) {
    var node = el("div", "m-get-pick");
    var head = el("div", "m-count m-get-head");
    var counted = el("span", "m-get-counted");
    head.append(el("h3", "m-count-title", "Главы"), counted, quick([["all", "все"], ["none", "ничего"]], function (how) {
      boxes().forEach(function (tick) {
        if (!tick.disabled) { tick.checked = how === "all"; paintTile(tick); }
      });
      changed();
    }));
    node.append(head);
    var chapters = f.list || [];
    var tomes = {}, order = [];
    chapters.forEach(function (c) {
      var key = String(Number(c.tome) || 0);
      if (!tomes[key]) { tomes[key] = []; order.push(key); }
      tomes[key].push(c);
    });
    order.forEach(function (key) {
      if (order.length > 1) { node.append(el("h4", "m-get-group", Number(key) ? "Том " + key : "Без тома")); }
      var grid = el("div", "m-ep-grid m-get-eps");
      tomes[key].forEach(function (c) { grid.append(tile(c)); });
      node.append(grid);
    });
    if (!chapters.length) { node.append(el("p", "m-get-note m-get-pad", "Глав у тайтла нет ни на одном из сайтов.")); }
    node.addEventListener("change", function (event) {
      if (event.target.matches("input[type='checkbox']")) { paintTile(event.target); changed(); }
    });

    function base(c) {
      if (c.have) { return {ok: false, mark: "есть", kind: "have"}; }
      if (c.wait) { return {ok: false, mark: "платная", kind: "wait"}; }
      if (c.from) {
        return others() ? {ok: true, mark: "с " + c.from, kind: "from"}
          : {ok: false, mark: c.paid ? "платная" : "нет", kind: "wait"};
      }
      return {ok: true, mark: "", kind: ""};
    }

    function tile(c) {
      var label = el("label", "m-ep m-get-ep");
      label.dataset.id = String(c.id);
      var tick = el("input", "m-sr");
      tick.type = "checkbox";
      tick.value = String(c.id);
      label.append(tick, el("span", "m-get-ep-n", c.n || "•"), el("span", "m-get-ep-mark", ""));
      tick.setAttribute("aria-label", c.label + (c.name ? " — " + c.name : ""));
      return label;
    }

    function boxes() { return Array.prototype.slice.call(node.querySelectorAll("input[type='checkbox']")); }

    function paint(mode) {
      var byId = {};
      chapters.forEach(function (c) { byId[String(c.id)] = c; });
      boxes().forEach(function (tick) {
        var c = byId[tick.value];
        var b = base(c);
        var was = tick.disabled;
        tick.disabled = !b.ok;
        if (!b.ok) { tick.checked = false; }
        else if (mode === "all" || (mode === "on" && b.kind === "from" && was)) { tick.checked = true; }
        var label = tick.closest(".m-get-ep");
        label.dataset.have = b.kind === "have" ? "whole" : "";
        label.querySelector(".m-get-ep-mark").textContent = b.mark;
        label.title = [c.label + (c.name ? " — " + c.name : ""), c.free_at ? "Платная — бесплатной станет " + c.free_at : "",
          c.from && b.ok ? "Скачается с " + c.from : ""].filter(Boolean).join("\n");
        paintTile(tick);
      });
    }
    paint("all");

    return {
      node: node,
      counted: counted,
      repaint: paint,
      chosen: function () {
        return boxes().filter(function (t) { return t.checked && !t.disabled; }).map(function (t) { return t.value; });
      },
      open: function () { return boxes().filter(function (t) { return !t.disabled; }).length; },
      waiting: function () { return chapters.filter(function (c) { return base(c).kind === "wait"; }).length; }
    };
  }

  // ── nhentai ───────────────────────────────────────────────────────────────────────────────────────────────────
  function showManga(ids) {
    foundBox.textContent = "";
    shown = {kind: "manga", id: 0};
    var card = el("div", "m-get-plain");
    card.append(el("h2", "m-get-title", ids.length === 1 ? "Галерея nhentai " + ids[0] : "Галереи nhentai: " + ids.length));
    var chips = el("p", "m-facts");
    ids.slice(0, 30).forEach(function (n) { chips.append(el("span", "", "№ " + n)); });
    card.append(chips, el("p", "m-get-note", "Галерея ляжет в папку манги со своими названиями и тегами; прерванная "
      + "докачается."));
    foundBox.append(card);
    var bar = goBar();
    bar.go.textContent = ids.length === 1 ? "Скачать" : "Скачать " + plural(ids.length, ["галерею", "галереи", "галерей"]);
    foundBox.append(bar.node);
    foundBox.hidden = false;
    bar.go.addEventListener("click", function () {
      bar.go.disabled = true;
      send(KINDS.manga.get, {text: ids.join(" ")}).then(function (reply) {
        window.LocalBooru.say(null, reply && reply.already ? "Уже в «Загрузках»" : reply && reply.queued
          ? (reply.queued > 1 ? "В очереди " + reply.queued + "-й" : "В очереди следующей") : "Скачивание началось");
        watch("manga", true);
        goingBox.scrollIntoView({block: "start", behavior: "smooth"});
      }).catch(function (err) { window.LocalBooru.say(null, err.message); })
        .then(function () { bar.go.disabled = false; });
    });
    shownCount = null;
  }

  // ── the downloads going ───────────────────────────────────────────────────────────────────────────────────────
  var jobs = {};                 // kind -> its last state
  var cards = {};                // kind -> its card on the page
  var polling = {};
  var shownCount = null;         // the video list's button: «В очередь» while another title downloads

  function watch(kind, now) {
    if (polling[kind]) { return; }
    polling[kind] = true;
    if (now) { tick(kind); } else { setTimeout(function () { tick(kind); }, 0); }
  }

  function tick(kind) {
    stateOf(KINDS[kind].state).then(function (st) {
      var was = jobs[kind];
      jobs[kind] = st;
      paintJob(kind, st);
      if (shownCount && shown && shown.kind === kind) { shownCount(); }
      var goes = RUNNING.indexOf(st.status) >= 0 || (!!KINDS[kind].unqueue && ((st.queue || []).length || st.restoring));
      if (goes) {
        setTimeout(function () { tick(kind); }, document.hidden ? 6000 : 1500);
        return;
      }
      polling[kind] = false;
      var ended = !!st.id && !!was && (RUNNING.indexOf(was.status) >= 0 || was.id !== st.id);
      if (ended && shown && shown.kind === kind && kind !== "manga") {
        refreshFound(kind);           // a download has ended: what was found shows what is in the folder now
      }
    }).catch(function () {
      setTimeout(function () { tick(kind); }, 5000);
    });
  }

  function refreshFound(kind) {
    var mine = shown;
    getJson(KINDS[kind].findState + "?after=999999999").then(function (st) {
      if (shown === mine && st.found && st.id === mine.id) { showFound(kind, st); }
    }).catch(function () { /* the list stays as it was */ });
  }

  function jobCard(kind) {
    if (cards[kind]) { return cards[kind]; }
    var node = el("article", "m-get-job");
    node.dataset.kind = kind;
    var head = el("div", "m-get-job-head");
    var title = el("b", "m-get-job-title");
    head.append(el("span", "m-get-job-kind", KINDS[kind].label), title);
    var text = el("p", "m-get-job-text");
    var meter = el("span", "m-bar m-get-job-bar");
    var fill = el("span");
    meter.append(fill);
    var sub = el("p", "m-get-job-sub");
    var list = el("ul", "m-get-job-list");
    var buttons = el("div", "m-get-job-buttons");
    var pause = el("button", "m-button m-quiet", "Пауза");
    pause.type = "button";
    var stop = el("button", "m-button m-quiet", "Остановить");
    stop.type = "button";
    var open = el("a", "m-button m-quiet", "Открыть");
    buttons.append(pause, stop, open);
    node.append(head, text, meter, sub, list, buttons);
    stop.addEventListener("click", function () {
      stop.disabled = true;
      send(KINDS[kind].stop, {}).then(function () { window.LocalBooru.say(null, "Останавливаю…"); watch(kind, true); })
        .catch(function (err) { window.LocalBooru.say(null, err.message); })
        .then(function () { stop.disabled = false; });
    });
    pause.addEventListener("click", function () {
      var st = jobs[kind] || {};
      pause.disabled = true;
      send(KINDS[kind].pause, {pause: !st.paused}).then(function (reply) {
        window.LocalBooru.say(null, reply.paused ? (kind === "manhwa" ? "Пауза: глава остановится, где она есть"
          : "Пауза: файл остановится, где он есть") : "Продолжаю");
        watch(kind, true);
      }).catch(function (err) { window.LocalBooru.say(null, err.message); })
        .then(function () { pause.disabled = false; });
    });
    cards[kind] = {node: node, title: title, text: text, meter: meter, fill: fill, sub: sub, list: list, pause: pause,
                   stop: stop, open: open};
    jobsBox.append(node);
    return cards[kind];
  }

  function paintJob(kind, st) {
    var soon = !!KINDS[kind].unqueue && st.restoring;
    var queue = KINDS[kind].unqueue ? st.queue || [] : [];
    if (!st.id && !soon && !queue.length) {
      if (cards[kind]) { cards[kind].node.hidden = true; }
      goingBox.hidden = !ORDER.some(function (k) { return cards[k] && !cards[k].node.hidden; });
      return;
    }
    var c = jobCard(kind);
    c.node.hidden = false;
    goingBox.hidden = false;
    var running = RUNNING.indexOf(st.status) >= 0;
    c.node.dataset.state = running ? (st.paused ? "paused" : "running") : st.status || "idle";
    c.title.textContent = String(st.label || "").replace(KINDS[kind].prefix, "").replace(/^«(.*)»$/, "$1")
      || (soon ? "С прошлого запуска" : "");
    c.stop.hidden = !running && !queue.length;
    c.stop.textContent = queue.length ? "Остановить всё" : "Остановить";
    c.pause.hidden = !KINDS[kind].pause || !running || st.status === "stopping";
    c.pause.textContent = st.paused ? "Продолжить" : "Пауза";
    c.list.textContent = "";
    var said = {text: "", sub: "", part: running ? 0.02 : 1, open: ""};
    if (kind === "video") { paintVideo(st, said, running, soon, queue, c); }
    else if (kind === "manhwa") { paintManhwa(st, said, running, soon, queue, c); }
    else { paintManga(st, said, running, soon, queue, c); }
    if (!running && st.status === "failed") { said.text = st.error || "Не вышло — причина в журнале на компьютере"; }
    c.text.textContent = said.text;
    c.text.hidden = !said.text;
    c.sub.textContent = said.sub;
    c.sub.hidden = !said.sub;
    c.meter.hidden = !running;
    c.fill.style.width = Math.max(2, Math.min(100, said.part * 100)).toFixed(1) + "%";
    c.open.hidden = running || !said.open;
    c.open.href = phoneLink(said.open);
    c.list.hidden = !c.list.children.length;
    // the downloads going first, then the ones that have ended (in the order of the kinds)
    ORDER.map(function (k) { return cards[k]; }).filter(Boolean).sort(function (a, b) {
      return (a.node.dataset.state === "running" || a.node.dataset.state === "paused" ? 0 : 1)
        - (b.node.dataset.state === "running" || b.node.dataset.state === "paused" ? 0 : 1);
    }).forEach(function (card) { jobsBox.append(card.node); });
  }

  function paintVideo(st, said, running, soon, queue, c) {
    var m = st.meter || {};
    if (running && m.count) {
      if (m.merging) {
        said.text = "Собираю озвучки в общие файлы серий: " + (m.name || "");
        said.part = Math.max(0, (Number(m.index) || 1) - 1) / m.count;
      } else {
        var part = m.total ? Math.min(1, m.done / m.total) : 0;
        said.part = Math.min(1, ((Number(m.index) || 1) - 1 + part) / m.count);
        var name = String(m.name || "Файл").replace(/\.[^.]+$/, "");
        var bits = [];
        if (m.total) { bits.push(Math.floor(part * 100) + "% (" + megabytes(m.done) + " из " + megabytes(m.total) + ")"); }
        else if (m.done) { bits.push(megabytes(m.done)); }
        if (m.speed) { bits.push(megabytes(m.speed) + "/с"); }
        said.text = (st.paused ? "Пауза — " : "") + name + (bits.length ? ": " + bits.join(", ") : "");
      }
      said.sub = "Всего: " + m.index + " из " + m.count;
    } else if (running) {
      said.text = st.line || "Начинаю…";
    } else if (st.id) {
      var sum = st.summary || {};
      var done = Number(sum.done) || 0, failed = Number(sum.failed_files) || 0, had = Number(sum.whole) || 0;
      said.text = (st.status === "stopped" || sum.stopped ? "Остановлено: " : "Готово: ")
        + (done ? "скачано " + plural(done, ["файл", "файла", "файлов"]) : "ничего не скачано")
        + (had ? ", уже были: " + had : "") + (failed ? ", не скачались: " + failed : "")
        + (Number(sum.merged) ? ", собрано в общие файлы: " + plural(Number(sum.merged), ["серия", "серии", "серий"]) : "");
      said.open = st.series_url || "";
    }
    if (soon) {
      said.sub = "С прошлого запуска программы продолжится: " + soon.titles.map(function (t) { return "«" + t + "»"; })
        .join(", ") + (soon["in"] > 0 ? " — через " + soon["in"] + " с" : " — сейчас");
    }
    queueLines("video", queue, c, function (q) { return plural(q.files, ["файл", "файла", "файлов"]); });
  }

  function queueLines(kind, queue, c, what) {
    queue.forEach(function (q, n) {
      var li = el("li");
      li.append(el("span", "", (n ? "Потом" : "Дальше") + ": «" + q.title + "»" + (what(q) ? " — " + what(q) : "")));
      var drop = el("button", "m-get-link", "убрать");
      drop.type = "button";
      drop.addEventListener("click", function () {
        send(KINDS[kind].unqueue, {id: q.id}).then(function () { watch(kind, true); })
          .catch(function (err) { window.LocalBooru.say(null, err.message); });
      });
      li.append(drop);
      c.list.append(li);
    });
  }

  // (v5.44.74) the manhwa: the chapter going, its strips, the speed, the counts — from the meter (no chapters' list)
  function paintManhwa(st, said, running, soon, queue, c) {
    var m = st.meter || {};
    var now = m.now;
    if (running && m.total) {
      var part = now && now.pages ? Math.min(1, now.got / now.pages) : 0;
      said.part = Math.min(1, ((m.done || 0) + (m.failed || 0) + part) / m.total);
      var bits = [];
      if (now) {
        bits.push(now.label + (now.from ? " (с " + now.from + ")" : "") + (now.pages ? ": " + now.got + " из " + now.pages
          + " " + word(now.pages, ["полосы", "полос", "полос"]) : ""));
      }
      if (m.speed && !st.paused) { bits.push(speedOf(m.speed)); }
      if (m.eta && !st.paused) { bits.push("осталось ≈ " + clockOf(m.eta)); }
      said.text = (st.paused ? "Пауза" + (bits.length ? " — " : "") : "") + (bits.join(", ") || (st.paused ? "" : st.line || "Начинаю…"));
      said.sub = "Глав: " + (m.done || 0) + " из " + m.total + (m.retrying ? ", ждут повтора: " + m.retrying : "")
        + (m.failed ? ", не скачались: " + m.failed : "") + (m.title && !st.find ? " · «" + m.title + "»" : "");
    } else if (running) {
      said.text = st.line || "Начинаю…";
    }
    var result = st.result;
    if (!running && result) {
      var failed = (result.failed || []).length;
      said.text = (result.got ? "Скачано: " + plural(result.got, ["глава", "главы", "глав"]) : "Новых глав не скачано")
        + (failed ? ", не скачались: " + failed : "") + (result.stopped ? " (остановлено)" : "")
        + (result.gave_up ? ". " + result.gave_up : "");
      said.open = result.open || "";
      (result.failed || []).slice(-3).forEach(function (x) {
        c.list.append(el("li", "m-get-warn", x.label + ": не скачалась" + (x.why ? " — " + x.why : "")));
      });
    }
    if (soon) {
      said.sub = "С прошлого запуска программы продолжится: " + soon.titles.map(function (t) { return "«" + t + "»"; })
        .join(", ") + (soon["in"] > 0 ? " — через " + soon["in"] + " с" : " — сейчас");
    }
    queueLines("manhwa", queue, c, function (q) { return q.count ? plural(q.count, ["глава", "главы", "глав"]) : ""; });
  }

  var MANGA_STATES = {wait: "в очереди", reading: "читаю описание…", done: "готово", had: "уже была скачана",
    failed: "не скачана", stopped: "остановлена — докачается"};

  function paintManga(st, said, running, soon, queue, c) {
    var items = st.items || [];
    var meter = st.meter || {};
    var done = items.filter(function (x) { return x.state === "done" || x.state === "had"; }).length;
    var now = items.filter(function (x) { return x.state === "pages" || x.state === "reading"; })[0];
    if (running) {
      said.part = items.length ? (done + (now && now.pages ? (now.page || 0) / now.pages : 0)) / items.length : 0.02;
      said.text = now ? (now.title || "Галерея " + now.number) + (now.state === "pages" ? ": страница "
        + (now.page || 1) + " из " + (now.pages || "?") : ": читаю описание…") : st.line || "Начинаю…";
      said.sub = items.length > 1 ? "Галерей: " + done + " из " + items.length : "";
    } else if (st.id) {
      var failed = items.filter(function (x) { return x.state === "failed"; }).length;
      said.text = (st.status === "stopped" ? "Остановлено: " : "Готово: ")
        + plural(done, ["галерея", "галереи", "галерей"]) + (failed ? ", не скачаны: " + failed : "");
      said.open = st.open || "";
    }
    if (meter && !items.length && running) { said.sub = ""; }
    if (soon) {
      said.sub = "С прошлого запуска программы продолжится: " + soon.titles.join(", ")
        + (soon.in > 0 ? " — через " + soon.in + " с" : " — сейчас");
    }
    // (v5.44.107) the galleries waiting — the queue below, each with «убрать»
    var waits = {};
    if (running) { queue.forEach(function (q) { waits[String(q.id)] = true; }); }
    items.filter(function (x) { return !(x.state === "wait" && waits[String(x.number)]); }).slice(-8).forEach(function (x) {
      var li = el("li", "m-get-gallery" + (x.state === "failed" ? " m-get-warn" : ""));
      var state = x.state === "pages" ? "страница " + (x.page || 1) + " из " + (x.pages || "?") : MANGA_STATES[x.state] || "";
      if (x.state === "failed" && x.why) { state += ": " + x.why; }
      if (x.cover) {
        var img = el("img", "m-get-gallery-pic");
        img.alt = "";
        img.loading = "lazy";
        img.src = x.cover;
        li.append(img);
      }
      var text = el("span", "m-get-gallery-text");
      text.append(el("b", "", x.title || "Галерея " + x.number), el("span", "", state));
      li.append(text);
      if (x.open && (x.state === "done" || x.state === "had") && items.length > 1) {
        var a = el("a", "m-get-link", "Открыть");
        a.href = phoneLink(x.open);
        li.append(a);
      }
      c.list.append(li);
    });
    if (running) { queueLines("manga", queue, c, function () { return ""; }); }
  }

  // ── the page ──────────────────────────────────────────────────────────────────────────────────────────────────
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    field.blur();
    ask(field.value);
  });
  field.addEventListener("paste", function () {
    setTimeout(function () { if (field.value.trim() && !findButton.disabled) { ask(field.value); } }, 30);
  });
  if (navigator.clipboard && navigator.clipboard.readText && window.isSecureContext) {
    pasteButton.hidden = false;
    pasteButton.addEventListener("click", function () {
      navigator.clipboard.readText().then(function (text) {
        if (!String(text || "").trim()) { window.LocalBooru.say(null, "В буфере обмена пусто"); return; }
        field.value = String(text).trim();
        ask(field.value);
      }, function () { window.LocalBooru.say(null, "Телефон не дал прочитать буфер — нажмите на поле и вставьте ссылку"); });
    });
  }
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { ORDER.forEach(function (kind) { watch(kind); }); }
  });

  ORDER.forEach(function (kind) { watch(kind); });
  if (box.dataset.autofind === "1" && field.value.trim()) {
    ask(field.value);
  } else if (field.value.trim()) {     // back to the page: the list found for this link, if it is still there
    var last = null;
    try { last = JSON.parse(stored("m-get-last") || "null"); } catch (e) { last = null; }
    if (last && last.text === field.value.trim() && KINDS[last.kind] && KINDS[last.kind].findState) {
      getJson(KINDS[last.kind].findState + "?after=999999999").then(function (st) {
        if (st.id !== last.id) { return; }
        if (RUNNING.indexOf(st.status) >= 0) {
          finding = last;
          busy(true);
          waitFound();
        } else if (st.found && !shown) {
          showFound(last.kind, st);
        }
      }).catch(function () { /* nothing shown: «Найти» again */ });
    }
  }
})();
