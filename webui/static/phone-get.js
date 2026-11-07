/* (v5.44.70) «Скачать по ссылке» on the phone (webui/pages/phone_get.py): a link → which of the three it is
   (/api/phone/get/what) → the computer's own «Найти» and «Скачать» of the videos (video_get), the manhwa (manhwa_get)
   and nhentai (manga_get): what was found, what to take, the downloads going with «Пауза» and «Остановить».  The
   computer's pages stay as they are — their answers are only read here.  (v5.44.74) The manhwa as the computer's page
   has it now: its chapters as tiles to tick, «Пауза» and the queue of its downloads; its state without the chapters'
   list (items=none: the card says the chapter going and the counts).
   (v5.44.122, the mockup E) The field understands what the computer's «Скачать → По ссылке» does (one parse —
   get_link.classify; the mark under the field says what it is): a name — the sites of manga searched at once
   (/api/manhwa/search: the titles as covers in their groups, one picked — its page /m/title); a post of a booru
   site — its card (the grabber's machinery: /api/grab/search → its picture, tags, «В галерею» — /api/grab/download). */
(function () {
  "use strict";

  var box = document.querySelector("[data-get]");
  if (!box || !window.LocalBooru) { return; }
  var send = window.LocalBooru.postJson;
  var form = box.querySelector("[data-get-form]");
  var field = form.querySelector("input[name='url']");
  var findButton = form.querySelector("button[type='submit']");
  var pasteButton = form.querySelector("[data-get-paste]");
  var markLine = form.querySelector("[data-get-mark]");
  var step = box.querySelector("[data-get-step]");
  var foundBox = box.querySelector("[data-get-found]");
  var RUNNING = ["starting", "running", "stopping"];
  // (v5.44.120) what the cards ask of each kind; «Пауза», «Остановить», «Не продолжать» — «Загрузки» (downloads.js)
  var KINDS = {
    video: {label: "Видео", find: "/api/videos/find", findState: "/api/videos/find-state", get: "/api/videos/download",
            state: "/api/videos/download-state", unqueue: "/api/videos/download/unqueue"},
    manhwa: {label: "Манхва", find: "/api/manhwa/find", findState: "/api/manhwa/find-state",
             get: "/api/manhwa/download", state: "/api/manhwa/download-state?items=none",
             unqueue: "/api/manhwa/download/unqueue"},
    manga: {label: "nhentai", get: "/api/manga/download", state: "/api/manga/download-state",
            unqueue: "/api/manga/download/unqueue"}   // (v5.44.107) a queue
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

  // (v5.44.122) what the text is (/api/phone/get/what — get_link.classify, as the computer's field): its answer, or an
  // error that keeps it (``data``: the mark of a link the mode refuses)
  function whatOf(text) {
    return fetch("/api/phone/get/what", {
      method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: text})
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.ok === false) {
          var err = new Error(data.error || "Компьютер ответил ошибкой " + response.status);
          err.data = data;
          throw err;
        }
        return data;
      });
    }, function () { throw new Error("Нет связи с компьютером"); });
  }

  var isLink = function (text) { return /^https?:\/\//i.test(String(text || "").trim()); };
  var same = function (text) { return String(text || "").replace(/\s+/g, " ").trim().toLowerCase(); };

  // the mark under the field: what the text is («Название — поиск по сайтам», «Галерея (booru) · Danbooru» …)
  var markGen = 0;
  var markTimer = 0;
  function showMark(label, bad) {
    markLine.textContent = label || "";
    markLine.hidden = !label;
    markLine.classList.toggle("bad", !!bad);
  }

  function markKind() {
    var text = field.value.trim();
    var gen = ++markGen;
    if (!text) { showMark(""); return; }
    whatOf(text).then(function (w) {
      if (gen === markGen) { showMark(w.label); }
    }, function (err) {
      var data = err.data || {};
      if (gen === markGen) { showMark(data.sfw ? data.label : "", true); }
    });
  }

  // (v5.44.117) the state of this page's own search: a title of the sites — by its number (each page has its own
  // search: the computer's «Найти» does not take the phone's place); the videos — as their page asks (the other chat's)
  function findAddress(kind, id) {
    return KINDS[kind].findState + (kind === "manhwa" && id ? "?find=" + encodeURIComponent(id) : "");
  }
  function withAfter(address, after) {
    return address + (address.indexOf("?") >= 0 ? "&" : "?") + "after=" + after;
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
    if (!text) { tell("Вставьте ссылку или напишите название", true); field.focus(); return; }
    keepField(text);
    foundBox.hidden = true;
    foundBox.textContent = "";
    shown = null;
    finding = null;
    dropHits();
    postGen += 1;
    clearTimeout(markTimer);
    markGen += 1;
    busy(true);
    tell("Смотрю, что это…");
    whatOf(text).then(function (w) {
      showMark(w.label);
      if (w.kind === "manga") {
        busy(false);
        tell("");
        showManga(w.ids);
        return null;
      }
      if (w.kind === "manhwa") {        // (v5.44.119) a title of a site: its one page (the phone's, title.py)
        store("m-get-last", JSON.stringify({kind: "title", text: text, url: w.url}));
        tell("Открываю страницу тайтла…");
        location.assign("/m/title?url=" + encodeURIComponent(w.url));
        return null;
      }
      if (w.kind === "name") {          // (v5.44.122) a name: the sites searched at once, the titles as covers
        searchName(w.q, text);
        return null;
      }
      if (w.kind === "booru") {         // (v5.44.122) a post of a booru site: its card, «В галерею»
        showPost(w, text);
        return null;
      }
      tell("Читаю страницу…");
      return send(KINDS[w.kind].find, {url: w.url}).then(function (reply) {
        finding = {kind: w.kind, id: reply.id, text: text};
        store("m-get-last", JSON.stringify(finding));
        waitFound();
      });
    }).catch(function (err) {
      busy(false);
      var data = err.data || {};
      var sfw = !!data.sfw || /SFW/.test(err.message);
      var extra = null;
      if (data.sfw) { showMark(data.label, true); }
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
    stateOf(findAddress(mine.kind, mine.id)).then(function (st) {
      if (finding !== mine) { return; }
      var going = RUNNING.indexOf(st.status) >= 0;
      // (v5.44.117) the title so far: the link's site's chapters at once, the other sites' as they answer (drawn again
      // each time something came — the chapters ticked stay ticked)
      if (st.id === mine.id && going && st.found) {
        if (!shown || shown.id !== st.id || shown.mark !== st.mark) {
          busy(false);
          tell("");
          showFound(mine.kind, st);
        }
        setTimeout(waitFound, 800);
        return;
      }
      if (st.id !== mine.id || going) {
        if (st.id === mine.id && st.line && !(shown && shown.id === st.id)) { tell(st.line); }
        setTimeout(waitFound, 700);
        return;
      }
      finding = null;
      busy(false);
      if (st.found) {
        tell("");
        if (!shown || shown.id !== st.id || shown.mark !== st.mark) { showFound(mine.kind, st); }
        return;
      }
      var why = st.failed || st.error || (st.status === "stopped" ? "Поиск остановлен" : "Не нашлось — проверьте ссылку");
      // (v5.44.116) the sites — the server's lists (manhwa_get.SITES; the ones of hentai only outside «SFW»)
      var sites = box.dataset.sites || "", adultSites = box.dataset.adultSites || "";
      tell(why, true, mine.kind === "video" && sites ? el("p", "m-muted", "Мангу и манхву программа берёт с " + sites
        + (adultSites ? ", а хентай — с " + adultSites : "") + "; с других сайтов — только видео.") : null);
    }).catch(function () {
      if (finding === mine) { setTimeout(waitFound, 1500); }
    });
  }

  function showFound(kind, st) {
    if (kind !== "video") { return; }       // (v5.44.119) a title of a site has its own page (the phone's title.py)
    foundBox.textContent = "";
    shown = {kind: kind, id: st.id, mark: st.mark || ""};
    showVideo(st);
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
    // (v5.44.106) a series of YummyAnime, (v5.44.109) of Hentasis: watched without downloading (/api/watch/can);
    // (v5.44.116) «Смотреть онлайн» — the computer's words too
    fetch("/api/watch/can?phone=1&url=" + encodeURIComponent(f.page_url || ""), { credentials: "same-origin" })
      .then(function (r) { return r.json(); }).then(function (can) {
        if (!can || !can.watch || view.about.querySelector(".m-watch-link")) return;
        var watching = el("a", "m-button m-quiet m-watch-link", "Смотреть онлайн");
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
            "В очереди следующим") + ": начнётся само" : "Скачивание началось — оно в «Загрузках»");
          watch("video", true);
          bumpCount();
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
        bumpCount();
      }).catch(function (err) { window.LocalBooru.say(null, err.message); })
        .then(function () { bar.go.disabled = false; });
    });
    shownCount = null;
  }

  // ── (v5.44.122) a name: the sites' searches at once, the titles as covers (manhwa_search, as the computer's) ──────
  var GROUPS = [["manga", "Манга"], ["strip", "Манхва, маньхуа и другие"]];
  var TITLES = ["тайтл", "тайтла", "тайтлов"];
  var hits = null;               // {id, query, picked, tiles, groups, list, head, sites} — the titles found by a name
  var hitsTimer = 0;

  function initial(text) { return String(text || "?").trim().charAt(0).toUpperCase() || "?"; }

  function searchName(q, text) {
    busy(true);
    tell("Ищу «" + q + "» на сайтах…");
    send("/api/manhwa/search", {q: q}).then(function (reply) {
      busy(false);
      tell("");
      store("m-get-last", JSON.stringify({kind: "name", text: text}));
      startHits(reply.id, q);
      pollHits();
    }).catch(function (err) {
      busy(false);
      tell(err.message, true);
    });
  }

  function dropHits() {
    clearTimeout(hitsTimer);
    hits = null;
  }

  function startHits(id, q) {
    clearTimeout(hitsTimer);
    foundBox.textContent = "";
    shown = {kind: "name", id: id};
    var head = el("div", "m-get-hits-head");
    var title = el("h2", "m-get-hits-title");
    var sites = el("ul", "m-get-hits-sites");
    sites.setAttribute("aria-label", "Что ответили сайты");
    head.append(title, sites);
    var list = el("div", "m-get-hits");
    list.setAttribute("aria-live", "polite");
    hits = {id: id, query: q, picked: "", tiles: {}, groups: {}, list: list, head: title, sites: sites, count: 0};
    GROUPS.forEach(function (pair) {
      var group = el("details", "m-get-hits-group");
      group.setAttribute("data-group", pair[0]);
      group.hidden = true;
      var summary = el("summary", "m-get-hits-sum");
      var name = el("span", "m-get-hits-name", pair[1]);
      var count = el("span", "m-get-hits-n");        // folded: «Манга — 3» and its first title
      var more = el("span", "m-get-hits-more");
      name.append(count);
      summary.append(name, more);
      var grid = el("div", "m-get-hits-grid");
      group.append(summary, grid);
      var entry = {node: group, grid: grid, count: count, more: more, chosen: false};
      summary.addEventListener("click", function () { entry.chosen = true; });     // opened or folded by hand
      hits.groups[pair[0]] = entry;
      list.append(group);
    });
    foundBox.append(head, list);
    foundBox.hidden = false;
  }

  function pollHits() {
    clearTimeout(hitsTimer);
    var mine = hits;
    if (!mine) { return; }
    getJson("/api/manhwa/search-state?id=" + encodeURIComponent(mine.id)).then(function (st) {
      if (hits !== mine) { return; }
      showHits(st);
      if (!st.done) { hitsTimer = setTimeout(pollHits, 500); }
    }).catch(function () {
      if (hits === mine) { hitsTimer = setTimeout(pollHits, 2000); }
    });
  }

  function showHits(st) {
    var titles = st.titles || [];
    var q = hits.query;
    hits.count = st.count || titles.length;
    foundBox.classList.toggle("m-get-busy", !st.done);
    hits.head.textContent = !st.done
      ? "Ищу «" + q + "»…" + (hits.count ? " Пока " + plural(hits.count, TITLES) : "")
      : hits.count ? "По названию «" + q + "» — " + plural(hits.count, TITLES)
        : "По названию «" + q + "» ничего не нашлось — попробуйте другое название (по-английски, по-русски) или ссылку";
    hits.sites.textContent = "";
    (st.sites || []).forEach(function (x) {
      var item = el("li", "m-get-hits-site " + (x.state || ""));
      item.append(el("b", "", x.label), document.createTextNode(" " + (x.state === "asking" ? "…"
        : x.state === "failed" ? "не ответил" : String(x.count))));
      if (x.error) { item.title = x.error; }
      hits.sites.append(item);
    });
    var lists = {manga: [], strip: []};
    var first = {};
    var seen = {};
    titles.forEach(function (t, k) {
      var key = t.kind === "manga" ? "manga" : "strip";
      lists[key].push(hitTile(t));
      if (!(key in first)) { first[key] = k; }
      seen[t.url] = true;
    });
    Object.keys(hits.tiles).forEach(function (url) {
      if (!seen[url]) { hits.tiles[url].remove(); delete hits.tiles[url]; }
    });
    // the group of the best title first, open; the other — folded into a line (its count, its first title)
    var order = GROUPS.map(function (pair) { return pair[0]; }).sort(function (a, b) {
      return (a in first ? first[a] : 1e9) - (b in first ? first[b] : 1e9);
    });
    order.forEach(function (key, n) {
      var group = hits.groups[key];
      var tiles = lists[key];
      var now = Array.prototype.slice.call(group.grid.children);
      if (now.length !== tiles.length || now.some(function (tile, k) { return tile !== tiles[k]; })) {
        tiles.forEach(function (tile) { group.grid.append(tile); });
      }
      group.node.hidden = !tiles.length;
      group.count.textContent = " — " + tiles.length;
      var top = tiles[0] && tiles[0].hit;
      group.more.textContent = top ? "«" + top.title + "»" + ((top.sites || []).length ? " · " + top.sites.map(function (x) {
        return x.label;
      }).join(", ") : "") : "";
      if (!group.chosen) { group.node.open = n === 0; }
      hits.list.append(group.node);
    });
  }

  function titleHref(t) {
    return "/m/title?url=" + encodeURIComponent(t.url) + "&k=" + (t.kind === "manga" ? "manga" : "manhwa")
      + (t.title ? "&t=" + encodeURIComponent(t.title) : "") + (t.rating ? "&r=" + t.rating : "");
  }

  function hitTile(t) {
    var tile = hits.tiles[t.url];
    if (!tile) {
      tile = el("a", "m-get-hit");
      var cover = el("span", "m-get-hit-cover");
      cover.append(el("span", "m-get-initial", initial(t.title)));
      tile.append(cover, el("b", "m-get-hit-name"), el("span", "m-get-hit-facts"), el("span", "m-get-hit-sites"));
      tile.addEventListener("click", function () { pickHit(tile); });     // the link opens the title's page
      hits.tiles[t.url] = tile;
    }
    tile.hit = t;
    tile.href = titleHref(t);
    var cover = tile.querySelector(".m-get-hit-cover");
    if ((t.cover || "") !== (tile.getAttribute("data-cover") || "")) {
      tile.setAttribute("data-cover", t.cover || "");
      cover.querySelectorAll("img").forEach(function (img) { img.remove(); });
      if (t.cover) {
        var img = el("img");
        img.alt = "";
        img.loading = "lazy";
        img.decoding = "async";
        img.onerror = function () { img.remove(); };
        img.src = "/api/manhwa/hit-cover?s=" + encodeURIComponent(hits.id) + "&c=" + encodeURIComponent(t.cover);
        cover.append(img);
      }
    }
    cover.title = t.cover_hidden ? "Обложка скрыта (SFW)" : "";
    var badge = cover.querySelector(".m-get-hit-badge");
    if (t.adult && !badge) { cover.append(el("span", "m-get-hit-badge", "18+")); }
    else if (!t.adult && badge) { badge.remove(); }
    tile.querySelector(".m-get-hit-name").textContent = t.title || "Без названия";
    var facts = [t.type, t.year || "", t.chapters ? t.chapters + " гл." : "",
      t.rating ? "★ " + String(t.rating).replace(".", ",") : ""].filter(Boolean);
    tile.querySelector(".m-get-hit-facts").textContent = facts.join(" · ");
    var sites = (t.sites || []).map(function (x) { return x.label; });
    tile.querySelector(".m-get-hit-sites").textContent = sites.join(", ");
    var on = t.url === hits.picked;
    tile.classList.toggle("picked", on);
    if (on) { tile.setAttribute("aria-current", "true"); } else { tile.removeAttribute("aria-current"); }
    tile.setAttribute("aria-label", [t.title].concat(facts, sites.length ? ["на " + sites.join(", ")] : [])
      .filter(Boolean).join(", "));
    return tile;
  }

  // a title picked — its one page (the link); back here — the titles of the name, it marked (restoreHits)
  function pickHit(tile) {
    if (!hits || !tile.hit) { return; }
    hits.picked = tile.hit.url;
    Object.keys(hits.tiles).forEach(function (url) {
      var on = url === hits.picked;
      hits.tiles[url].classList.toggle("picked", on);
      if (on) { hits.tiles[url].setAttribute("aria-current", "true"); } else { hits.tiles[url].removeAttribute("aria-current"); }
    });
    store("m-get-last", JSON.stringify({kind: "title", text: field.value.trim() || hits.query, url: tile.hit.url}));
    tell("Открываю страницу тайтла…");
  }

  // back to the page (a title read, a reload): the name searched before — its titles as they were, the picked one
  // marked; the search let go meanwhile (or another name since) — searched anew
  function restoreHits(text, url) {
    getJson("/api/manhwa/search-state?id=last").then(function (st) {
      if (hits || finding) { return; }
      if (!st.id || st.gone || same(st.query) !== same(text)) { ask(text); return; }
      startHits(st.id, st.query);
      hits.picked = url || "";
      showHits(st);
      if (!st.done) { pollHits(); }
    }).catch(function () { ask(text); });
  }

  // ── (v5.44.122) a post of a booru site: its card — the grabber's machinery, as the computer's «Галерея (booru)»
  // opens a post by its link (/api/grab/search → the one card; its picture through the program; «В галерею» — a
  // download of that one post, seen in «Загрузки») ───────────────────────────────────────────────────────────────
  var OWNED = {file: "Уже в галерее", post: "Похожая уже в галерее — найдена обратным поиском, файл может отличаться",
               trash: "В корзине — вернуть можно там"};
  var TAGS = ["тег", "тега", "тегов"];
  var TAGS_SHOWN = 8;
  var postGen = 0;               // the post shown: a later answer for one not shown any more is dropped
  var TICK = "M5 12.5l4.5 4.5L19 7.5";

  function icon(d, size) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", String(size || 16));
    svg.setAttribute("height", String(size || 16));
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    d.split("|").forEach(function (one) {
      var path = document.createElementNS(ns, "path");
      path.setAttribute("d", one);
      svg.append(path);
    });
    return svg;
  }

  function showPost(w, text) {
    var gen = ++postGen;
    tell("Спрашиваю пост у сайта…");
    store("m-get-last", JSON.stringify({kind: "booru", text: text}));
    send("/api/grab/search", {q: w.url}).then(function (data) {
      if (gen !== postGen) { return; }
      busy(false);
      var card = (data.cards || [])[0];
      if (!card) { tell(data.error_note || "Пост не получен", true); return; }
      tell("");
      drawPost(data.search || {}, card, gen);
    }).catch(function (err) {
      if (gen !== postGen) { return; }
      busy(false);
      tell(err.message, true);
    });
  }

  function drawPost(search, c, gen) {
    foundBox.textContent = "";
    shown = {kind: "booru", id: search.id};
    var media = function (v) {
      return "/grab/media?s=" + encodeURIComponent(search.id) + "&k=" + encodeURIComponent(c.key) + "&v=" + v;
    };
    var site = (c.sites || [])[0] || "";
    showMark("Галерея (booru)" + (site ? " · " + site : ""));
    var card = el("article", "m-get-post");
    var pic = el("span", "m-get-post-pic");
    var img = el("img");
    img.alt = "";
    img.decoding = "async";
    img.onerror = function () {          // no larger copy: the preview; none at all — no picture
      if (img.getAttribute("data-tried")) { pic.hidden = true; return; }
      img.setAttribute("data-tried", "1");
      img.src = media("preview");
    };
    img.src = media(c.video ? "preview" : "sample");
    pic.append(img);
    if (c.video) { pic.append(el("span", "m-get-post-note", "Видео — здесь только кадр")); }
    var body = el("div", "m-get-post-body");
    body.append(el("h2", "m-get-title", (c.post ? "Пост " + c.post : "Пост") + (site ? " · " + site : "")));
    var facts = [c.w && c.h ? c.w + " × " + c.h : "", c.ext ? c.ext.toUpperCase() : "",
      (c.tags || []).length ? plural(c.tags.length, TAGS) : ""].filter(Boolean);
    if ((c.sites || []).length > 1) { facts.push("ещё на: " + c.sites.slice(1).join(", ")); }
    body.append(el("p", "m-get-post-facts", facts.join(" · ")));
    var tags = el("div", "m-get-post-tags");
    body.append(tags);
    var state = el("p", "m-get-post-state");
    state.setAttribute("role", "status");
    body.append(state);
    var two = el("div", "m-get-two");
    var go = el("button", "m-button m-get-post-go");
    go.type = "button";
    var onSite = el("a", "m-button m-quiet m-get-post-site", "На сайте");
    onSite.href = c.url || "#";
    onSite.target = "_blank";
    onSite.rel = "noopener noreferrer";
    two.append(go, onSite);
    body.append(two);
    card.append(pic, body);
    foundBox.append(card);
    foundBox.hidden = false;
    drawTags(tags, c.tags || [], false);
    paintPost(c, state, go);
    go.addEventListener("click", function () { getPost(search, c, state, go, gen); });
    // the tags by kind as the site gives them (a site of the Gelbooru kind is asked for them now) — as the computer's
    getJson("/api/grab/card?s=" + encodeURIComponent(search.id) + "&k=" + encodeURIComponent(c.key)).then(function (r) {
      if (gen !== postGen) { return; }
      var named = [];
      (r.groups || []).forEach(function (g) { (g[2] || []).forEach(function (name) { named.push([name, g[0]]); }); });
      if (named.length) { drawTags(tags, named, tags.classList.contains("all")); }
    }).catch(function () { /* the tags of the card stay */ });
  }

  function drawTags(box, list, all) {
    box.textContent = "";
    box.classList.toggle("all", !!all);
    list.forEach(function (pair, k) {
      var cat = String(pair[1] || "general").replace(/[^a-z]/g, "");
      var tag = el("span", "m-tag m-cat-" + cat, String(pair[0]).replace(/_/g, " "));
      if (!all && k >= TAGS_SHOWN) { tag.hidden = true; }
      box.append(tag);
    });
    if (!all && list.length > TAGS_SHOWN) {
      var more = el("button", "m-get-post-more", "ещё " + (list.length - TAGS_SHOWN));
      more.type = "button";
      more.addEventListener("click", function () { drawTags(box, list, true); });
      box.append(more);
    }
  }

  // what the gallery has of it, and whether «В галерею» can take it
  function paintPost(c, state, go) {
    state.textContent = "";
    state.className = "m-get-post-state";
    var blocked = (c.blocked || []).length > 0;
    go.textContent = "";
    if (blocked) {
      state.classList.add("warn");
      state.append(document.createTextNode("Теги из блоклиста подписок и «Галереи (booru)»: " + c.blocked.join(", ")
        + " — такой пост не скачивается. Блоклист — на компьютере, «Подписки»."));
    } else if (!c.original) {
      state.classList.add("warn");
      state.append(document.createTextNode("Сайт не даёт оригинал этого поста — скачивать нечего."));
    } else if (c.owned && OWNED[c.owned]) {
      state.append(icon(TICK, 15), document.createTextNode(OWNED[c.owned]));
    } else {
      state.append(icon(TICK, 15), document.createTextNode("Такой картинки в галерее нет"));
    }
    if (c.owned && c.image_id) {
      var open = el("a", "m-link m-get-post-open", "Открыть");
      open.href = "/m/post/" + c.image_id;
      state.append(document.createTextNode(" · "), open);
    }
    var had = c.owned === "file";
    go.append(icon(had ? TICK : "M12 4v11|M7 10.5l5 5 5-5|M5 19.5h14", 16), el("span", "", had ? "В галерее" : "В галерею"));
    go.disabled = had || blocked || !c.original;
  }

  function getPost(search, c, state, go, gen) {
    go.disabled = true;
    send("/api/grab/download", {s: search.id, keys: [c.key]}).then(function (reply) {
      window.LocalBooru.say(null, "Скачивание началось — оно в «Загрузках»");
      bumpCount();
      go.querySelector("span").textContent = "Скачивается…";
      followGrab(reply.id, search, c, state, go, gen);
    }).catch(function (err) {
      window.LocalBooru.say(null, err.message);
      go.disabled = false;
    });
  }

  // the download of the post (a check of subscriptions for one run): when it has ended — what the gallery has now
  function followGrab(job, search, c, state, go, gen) {
    var troubles = 0;
    var poll = function () {
      if (gen !== postGen) { return; }
      getJson("/api/subs/state?after=999999999").then(function (st) {
        troubles = 0;
        if (String(st.id) === String(job) && ["starting", "running", "waiting", "stopping"].indexOf(st.status) >= 0) {
          setTimeout(poll, 1200);
          return;
        }
        return send("/api/grab/owned", {s: search.id}).then(function (r) {
          if (gen !== postGen) { return; }
          var m = (r.marks || {})[c.key] || {};
          c.owned = m.owned || "";
          c.image_id = m.image_id || 0;
          paintPost(c, state, go);
          if (c.owned !== "file") { window.LocalBooru.say(null, "Пост не скачался — почему, видно в «Загрузках»"); }
        });
      }).catch(function () {
        if (++troubles < 20) { setTimeout(poll, 2500); }
      });
    };
    setTimeout(poll, 800);
  }

  // ── the downloads going, as the card needs them (v5.44.120: «Загрузки» are a page of their own — /m/get/downloads,
  // downloads.js; here only «В очередь» while another title downloads, and the card read again when its download has
  // ended) ─────────────────────────────────────────────────────────────────────────────────────────────────────────
  var jobs = {};                 // kind -> its last state
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
    getJson(withAfter(findAddress(kind, mine.id), 999999999)).then(function (st) {
      if (shown === mine && st.found && st.id === mine.id) { showFound(kind, st); }
    }).catch(function () { /* the list stays as it was */ });
  }

  // (v5.44.120) «Скачать» pressed: the number of «Загрузки» (the chip on top, ⬇ of the bar) at once
  function bumpCount() {
    getJson("/api/downloads/count").then(function (data) {
      var n = Number(data.count) || 0;
      document.querySelectorAll("[data-dl-n]").forEach(function (pill) {
        pill.textContent = n ? String(n) : "";
        pill.hidden = !n;
      });
    }).catch(function () { /* the next question of phone.js says it */ });
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
  field.addEventListener("input", function () {          // (v5.44.122) what it is, said under the field as it is typed
    clearTimeout(markTimer);
    markTimer = setTimeout(markKind, 300);
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

  // (v5.44.119) back from a title's page: the line to open it again (not that page again by itself)
  function titleLine(url) {
    var again = el("a", "m-link", "открыть снова");
    again.href = "/m/title?url=" + encodeURIComponent(url);
    step.textContent = "";
    step.className = "m-get-step";
    step.append(el("span", "m-get-step-text", "Этот тайтл — на своей странице: "), again);
    step.hidden = false;
  }
  // …by the history, the page kept as it was left («Открываю…», «Найти» held): not busy any more ((v5.44.122) a title
  // picked among the ones of a name: they are there as they were)
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted || finding) { return; }
    var was = null;
    try { was = JSON.parse(stored("m-get-last") || "null"); } catch (e) { was = null; }
    if (!was || was.kind !== "title" || was.text !== field.value.trim()) { return; }
    busy(false);
    if (isLink(was.text)) { titleLine(was.url || was.text); } else { tell(""); }
  });

  if (field.value.trim()) { markKind(); }
  if (box.dataset.autofind === "1" && field.value.trim()) {
    ask(field.value);
  } else if (field.value.trim()) {     // back to the page: the list found for this link, if it is still there
    var last = null;
    try { last = JSON.parse(stored("m-get-last") || "null"); } catch (e) { last = null; }
    var asked = field.value.trim();
    if (last && last.kind === "title" && last.text === asked) {
      // a link — the line to open its page again; (v5.44.122) a name — the titles found by it, the picked one marked
      if (isLink(asked)) { titleLine(last.url || last.text); } else { restoreHits(asked, last.url); }
    } else if (last && last.kind === "name" && last.text === asked) {
      restoreHits(asked, "");
    } else if (last && last.kind === "booru" && last.text === asked) {
      ask(asked);                      // (v5.44.122) the post's card again (one question of its site)
    } else if (last && last.text === field.value.trim() && KINDS[last.kind] && KINDS[last.kind].findState) {
      getJson(withAfter(findAddress(last.kind, last.id), 999999999)).then(function (st) {
        if (st.gone || st.id !== last.id) { return; }
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
