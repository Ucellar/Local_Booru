// Local Booru manga pages: «Скачать с nhentai» (/manga/get) — galleries by their links or numbers, downloaded by the
// program one after another (core/manga_download.py, a page task): what the field holds (chips under it, asked of the
// program as it is typed), each gallery's line with its cover and pages, the whole progress, «Остановить», the journal
// under «Подробности».
// The lists of the folder's tags, artists, characters and parodies (/manga/tags …): the field hides the chips that do
// not have its text (and the letters left empty).
(() => {
  "use strict";

  // «Найти среди …» over nhentai's list of the manga's tags (webui/tag_lists.py): the chips of the page
  const field = document.querySelector(".taglist-nh .tag-filter");
  if (!field) return;
  const index = field.closest(".taglist-nh");
  const chips = Array.from(index.querySelectorAll(".tag-chips .tagchip"));
  const names = chips.map((chip) => (chip.querySelector(".name") || chip).textContent.trim().toLowerCase());
  const letters = Array.from(index.querySelectorAll(".tag-letter"));
  const jump = index.querySelector(".alphabetical-pagination");
  const none = index.querySelector(".tag-none");
  field.addEventListener("input", () => {
    const words = field.value.trim().toLowerCase();
    let shown = 0;
    chips.forEach((chip, n) => {
      const on = !words || names[n].includes(words);
      chip.hidden = !on;
      if (on) shown += 1;
    });
    letters.forEach((section) => { section.hidden = !section.querySelector(".tagchip:not([hidden])"); });
    if (jump) jump.hidden = Boolean(words);
    if (none) none.hidden = shown > 0;
  });
})();

(() => {
  "use strict";

  const LB = window.LocalBooru;
  const box = document.getElementById("manga-get");
  if (!LB || !box || !LB.watchTask) return;
  const { actions, postJson, say } = LB;
  const form = box.querySelector('[data-form="manga-get"]');
  const field = form.querySelector("textarea");
  const parsed = form.querySelector(".mget-parsed");
  const go = form.querySelector('button[type="submit"]');
  const job = box.querySelector('[data-job="download"]');
  const stopButton = job.querySelector('[data-action="manga-get-stop"]');
  const meterBox = job.querySelector(".mmeter");
  const itemsBox = job.querySelector(".mget-items");
  const result = job.querySelector(".mget-result");
  const open = job.querySelector(".mget-open");
  const RUNNING = ["starting", "running", "stopping"];
  let named = [];                 // the galleries the field names now
  let unclear = 0;                // pieces of the field not understood: the program would refuse the download
  let busy = false;

  const size = (bytes) => {
    const n = Number(bytes) || 0;
    return n >= 1048576 ? (n / 1048576).toFixed(1).replace(".", ",") + " МБ" : Math.round(n / 1024) + " КБ";
  };
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

  // ── what the field holds: chips of the galleries, the pieces not understood ──
  let parseTimer = 0;
  let parseSeq = 0;
  async function readField() {
    const text = field.value;
    const mine = ++parseSeq;
    if (!text.trim()) { named = []; unclear = 0; showParsed([], [], 0); return; }
    try {
      const reply = await postJson("/api/manga/parse", { text });
      if (mine !== parseSeq) return;
      named = reply.ids || [];
      unclear = (reply.bad || []).length + (reply.max && named.length > reply.max ? 1 : 0);
      showParsed(named, reply.bad || [], reply.max || 0);
    } catch (_) {
      /* the chips stay as they were; «Скачать» asks the program anyway */
    }
  }
  function showParsed(ids, bad, most) {
    parsed.textContent = "";
    ids.slice(0, 40).forEach((n) => parsed.append(element("span", "mchip", "#" + n)));
    if (ids.length > 40) parsed.append(element("span", "mchip more", "ещё " + (ids.length - 40)));
    bad.forEach((piece) => parsed.append(element("span", "mchip bad", "не понял: «" + piece + "»")));
    if (most && ids.length > most) parsed.append(element("span", "mchip bad", "за раз — не больше " + most));
    parsed.hidden = !parsed.children.length;
    countButton();
  }
  function countButton() {
    go.disabled = busy || unclear > 0;
    go.textContent = named.length ? "Скачать " + plural(named.length, ["галерею", "галереи", "галерей"]) : "Скачать";
    go.title = unclear ? "Уберите из поля то, что программа не поняла" : "";
  }
  field.addEventListener("input", () => {
    clearTimeout(parseTimer);
    parseTimer = setTimeout(readField, 250);
  });

  // ── the download: a line for each gallery, the whole of it in the bar ──
  const STATES = { wait: "в очереди", reading: "читаю описание…", pages: "", done: "готово", had: "уже была скачана",
    failed: "не скачана", stopped: "остановлена — докачается в следующий раз" };

  function line(item, running) {
    const row = element("li", "mget-item");
    let state = item.state || "wait";
    if (state === "wait" && !running) state = "skipped";
    row.dataset.state = state;
    const cover = element("span", "mget-cover");
    if (item.cover) {
      const img = element("img");
      img.alt = "";
      img.loading = "lazy";
      img.src = item.cover;
      cover.append(img);
    } else {
      cover.append(element("span", "mget-cover-n", "#"));
    }
    const text = element("div", "mget-text");
    const title = element(item.open ? "a" : "span", "mget-title", item.title || "Галерея " + item.number);
    if (item.open) title.href = item.open;
    text.append(title);
    const facts = ["#" + item.number];
    if (item.pages) facts.push(plural(item.pages, ["страница", "страницы", "страниц"]));
    if (item.bytes) facts.push(size(item.bytes));
    text.append(element("span", "mget-facts", facts.join(", ")));
    const status = element("div", "mget-state");
    let said = STATES[state] || "";
    if (state === "pages") said = "страница " + (item.page || 1) + " из " + (item.pages || "?");
    if (state === "skipped") said = "не начата — остановлено раньше";
    if (state === "failed" && item.why) said += ": " + item.why;
    status.append(element("span", "mget-said", said));
    if (state === "pages" && item.pages) {
      const bar = element("span", "mget-bar");
      const fill = element("i");
      fill.style.width = Math.round(100 * Math.max(0, (item.page || 1) - 1) / item.pages) + "%";
      bar.append(fill);
      status.append(bar);
    }
    if (item.open && (state === "done" || state === "had")) {
      const link = element("a", "mbutton", "Открыть");
      link.href = item.open;
      status.append(link);
    }
    row.append(cover, text, status);
    return row;
  }

  function showItems(st, running) {
    const items = st.items || [];
    itemsBox.textContent = "";
    items.forEach((item) => itemsBox.append(line(item, running)));
    itemsBox.hidden = !items.length;
  }

  function showMeter(m) {
    if (!m || !m.galleries) { meterBox.hidden = true; return; }
    meterBox.hidden = false;
    const page = m.pages ? Math.max(0, (Number(m.page) || 1) - 1) / m.pages : 0;
    const whole = Math.min(1, ((Number(m.gallery) || 1) - 1 + page) / m.galleries);
    meterBox.querySelector("progress").value = Math.round(whole * 1000);
    const bits = [];
    if (m.galleries > 1) bits.push("галерея " + m.gallery + " из " + m.galleries);
    if (m.pages) bits.push("страница " + m.page + " из " + m.pages);
    if (m.bytes) bits.push(size(m.bytes));
    const text = bits.join(", ");
    meterBox.querySelector(".mmeter-text").textContent = text ? text.charAt(0).toUpperCase() + text.slice(1) : "Читаю описание…";
  }

  function showResult(st, running) {
    if (running || !["done", "stopped", "failed"].includes(st.status)) { result.hidden = true; return; }
    const items = st.items || [];
    const done = items.filter((i) => i.state === "done" || i.state === "had").length;
    const failed = items.filter((i) => i.state === "failed").length;
    let text;
    if (st.status === "failed") text = st.error || "Скачивание прервалось — причина в подробностях";
    else {
      text = (st.status === "stopped" ? "Остановлено: " : "Готово: ")
        + (done ? "скачано " + plural(done, ["галерея", "галереи", "галерей"]) : "ничего не скачано");
      if (failed) text += ", не скачано: " + failed + " — причины в строках выше";
    }
    result.textContent = text;
    result.className = "mget-result " + (st.status === "failed" || (failed && !done) ? "bad" : failed || st.status === "stopped" ? "part" : "ok");
    result.hidden = false;
  }

  const watcher = LB.watchTask(job, "/api/manga/download-state", {
    onState(st, running) {
      busy = running;
      countButton();
      stopButton.hidden = !running;
      showMeter(running ? st.meter : null);
      showItems(st, running);
      showResult(st, running);
      open.hidden = running || !st.open;
      if (st.open) open.querySelector("a").href = st.open;
    },
    onEnd() { say(go, ""); say(stopButton, ""); },
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = field.value.trim();
    if (!text) { say(go, "Вставьте ссылку на галерею или её номер", true); field.focus(); return; }
    try {
      const reply = await postJson("/api/manga/download", { text });
      say(go, "");
      busy = true;
      countButton();
      job.hidden = false;
      open.hidden = true;
      result.hidden = true;
      itemsBox.textContent = "";
      watcher.started();
      if (reply.count) job.scrollIntoView({ block: "nearest" });
    } catch (err) {
      say(go, err.message, true);
    }
  });

  actions["manga-get-stop"] = (el) => postJson("/api/manga/download/stop", {})
    .then(() => say(el, "Останавливаю…")).catch((err) => say(el, err.message, true));

  if (field.value.trim()) readField();
})();

// (v5.44) сборки of the manga folder: «Сборками / Каждую отдельно» on «Вся манга»; on a title's page «Убрать из
// сборки», «Пусть решает программа» and «В сборку с …» (core/manga_works.py keeps what was done by hand)
(() => {
  "use strict";

  const LB = window.LocalBooru;
  if (!LB) return;
  const { actions, postJson, say } = LB;
  actions["manga-sets"] = async (el) => {
    try {
      await postJson("/api/prefs", { manga_sets: el.dataset.value === "off" ? "off" : "on" });
      location.reload();
    } catch (err) {
      say(el, err.message, true);
    }
  };
  async function change(el, body) {
    try {
      const answer = await postJson("/api/manga/" + el.dataset.id + "/set", body);
      say(el, answer.set ? "Готово" : "Готово: манга не в сборке");
      setTimeout(() => location.reload(), 400);
    } catch (err) {
      say(el, err.message, true);
    }
  }
  actions["manga-set"] = (el) => change(el, { do: el.dataset.do });
  const form = document.querySelector('[data-form="manga-set"]');
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      change(form, { do: "with", with: form.querySelector("select").value });
    });
  }
})();

