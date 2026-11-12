// (v5.44.78) «Настройки → Манга и манхва» (webui/pages/manga_sites.py, core/manhwa_access.py): the addresses of the sites
// of manga and manhwa (a site that moved) and the owner's sign-in — cookies from a file read here and sent to the
// program, MangaLib's token pasted.  What comes back is only what there is (how many cookies, until when the token
// lasts): no value is ever shown; the token's field is emptied as soon as it is sent.
// (v5.44.161) The sites are one table, a site's row opens («настроить»); the addresses and the language of MangaDex's
// chapters are saved by the page's one «Сохранить» (settings.js: the form says where — data-save), the sign-in — at
// once, by its own buttons here.
// (v5.44.100) The sign-in from the browser: the extension «Local Booru Companion» 0.10.0 takes it by itself — the page
// says whether it is there, «Открыть сайт» (through the extension, else the usual way), «Обновить вход» (the
// extension sends it now; the page waits for its answer), and keeps what there is fresh while it is open.
(() => {
  "use strict";
  const LB = window.LocalBooru;
  const box = document.getElementById("set-manga-sites");
  if (!LB || !box) return;
  const { actions, postJson, say } = LB;
  const confirmed = LB.confirmed || (() => true);
  const form = box.querySelector("[data-ms-addresses]");
  const MAX_FILE = 2000000;

  const day = (stamp) => new Date(stamp * 1000).toLocaleDateString("ru-RU");
  const moment = (stamp) => new Date(stamp * 1000).toLocaleString("ru-RU", { day: "numeric", month: "numeric",
    hour: "2-digit", minute: "2-digit" });
  let browser = { connected: false };            // (v5.44.100) the extension: is it there now
  let answers = {};                              // site → what the extension sent last
  function word(n, forms) {
    const d = n % 10, h = n % 100;
    return d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2];
  }

  // ── what there is ──
  function show(signin) {
    box.querySelectorAll("li.ms-site").forEach((li) => {
      const mine = (signin || {})[li.dataset.site] || {};
      const parts = [];
      if (mine.cookies) {
        parts.push(`cookies: ${mine.cookies} ${word(mine.cookies, ["штука", "штуки", "штук"])}` +
          (mine.domains && mine.domains.length ? ` (${mine.domains.join(", ")})` : "") +
          (mine.at && mine.from === "browser" ? `, из браузера ${moment(mine.at)}` :
            mine.at && !mine.token ? `, загружены ${day(mine.at)}` : ""));
      }
      if (mine.token) {
        const ended = mine.until && mine.until * 1000 < Date.now();
        parts.push(ended ? "токен закончился — возьмите новый" : "токен есть" + (mine.until ? `, до ${day(mine.until)}` : ""));
      }
      // (v5.44.161) the sign-in in a word on the site's row (whose chapters need one: data-need), the rest inside
      const state = li.querySelector("[data-ms-state]");
      const has = parts.length > 0;
      const lib = (signin || {}).mangalib || {};
      const shared = !has && li.dataset.site === "hentailib" && !!lib.token;
      const need = li.dataset.need || "";
      state.textContent = has ? "вход есть" : shared ? "общий с MangaLib" : need === "must" ? "нужен вход"
        : need === "some" ? "для 18+ — вход" : "не нужен";
      state.className = "ms-state sx-chip" + (has || shared ? " ok on" : need === "must" ? " need" : "");
      li.classList.toggle("signed", has || shared);            // «войти» of a site that shows nothing without it
      const detail = li.querySelector("[data-ms-detail]");
      if (detail) detail.textContent = has ? "Вход: " + parts.join("; ") + "." : shared ? "Вход общий с MangaLib."
        : "Вход не задан.";
      li.querySelectorAll('[data-action="ms-forget"]').forEach((b) => {
        b.hidden = b.dataset.what === "token" ? !mine.token : !mine.cookies;
      });
      const missing = li.querySelector("[data-ms-missing]");       // (v5.44.100) a domain the browser kept from it
      const told = answers[li.dataset.site] || {};
      if (missing) {
        missing.hidden = !(told.missing && told.missing.length);
        missing.textContent = missing.hidden ? "" : `Расширению не разрешён ${told.missing.join(", ")} — значок ` +
          "расширения → «Ещё» → «Разрешить сайты манги».";
      }
    });
  }

  function showBrowser(st) {                     // (v5.44.100) whether the extension is there
    browser = st.browser || { connected: false };
    answers = st.answers || {};
    const line = box.querySelector("[data-ms-companion]");
    if (line && st.browser_words) line.textContent = st.browser_words;
    if (line) line.classList.toggle("on", !!browser.connected);
  }

  async function refresh(quiet) {
    try {
      const r = await fetch("/api/manga-sites/state", { credentials: "same-origin" });
      const st = await r.json();
      showBrowser(st);
      show(st.signin);
      return st;
    } catch (err) {
      if (!quiet) say(box.querySelector(".ms-site .tool-actions"), "Не узнать, что сохранено: " + err.message, true);
      return null;
    }
  }

  // (v5.44.100) what the extension brings while the page is open (the owner signs in on a site it opened)
  setInterval(() => { if (!document.hidden && document.body.contains(box)) refresh(true); }, 5000);

  // ── (v5.44.79) the language of MangaDex's chapters: said in its row («главы: русский») as it is chosen ──
  let languages = {};
  try { languages = JSON.parse(box.dataset.msLanguages || "{}"); } catch (_) { languages = {}; }
  const languageField = form.elements.namedItem("language");
  const languageSaid = box.querySelector("[data-ms-lang]");
  function sayLanguage() {
    if (languageField && languageSaid) languageSaid.textContent = " · главы: " + (languages[languageField.value] || languageField.value);
  }
  if (languageField) languageField.addEventListener("change", sayLanguage);

  // ── the addresses: saved by the page's «Сохранить» (settings.js); a site's row says its address as it is typed ──
  form.addEventListener("input", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== "text" || !input.name || input.name.endsWith("_api")) return;
    const host = input.closest("li.ms-site").querySelector("[data-ms-host]");
    const typed = (input.value.trim() || input.placeholder).replace(/^[a-z]+:\/\//i, "").replace(/\/.*$/, "");
    if (host && typed) host.textContent = typed;
  });
  // a site's row opened: the others close (one at a time — the table stays a table)
  form.addEventListener("toggle", (event) => {
    const opened = event.target;
    if (!(opened instanceof HTMLDetailsElement) || !opened.open || !opened.classList.contains("ms-more")) return;
    if (form.closest("[data-finding]")) return;
    form.querySelectorAll("details.ms-more[open]").forEach((other) => { if (other !== opened) other.open = false; });
  }, true);

  // ── cookies from a file ──
  box.addEventListener("change", async (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-ms-file]")) return;
    const li = input.closest("li.ms-site");
    const file = input.files && input.files[0];
    input.value = "";
    if (!li || !file) return;
    if (file.size > MAX_FILE) {
      say(li.querySelector(".tool-actions"), "Файл слишком большой для cookies", true);
      return;
    }
    say(li.querySelector(".tool-actions"), "Читаю файл…");
    try {
      const text = await file.text();
      const reply = await postJson("/api/manga-sites/cookies", { site: li.dataset.site, text });
      show(reply.signin);
      say(li.querySelector(".tool-actions"), `Взято cookies: ${reply.cookies}` +
        (reply.other ? ` (других сайтов в файле: ${reply.other} — не взяты)` : ""));
    } catch (err) {
      say(li.querySelector(".tool-actions"), err.message, true);
    }
  });

  // ── MangaLib's token ──
  actions["ms-token"] = async (button) => {
    const li = button.closest("li.ms-site");
    const field = li.querySelector("[data-ms-token]");
    const text = field.value.trim();
    field.value = "";
    if (!text) {
      field.focus();
      say(li.querySelector(".tool-actions"), "Вставьте значение «auth» в поле токена", true);
      return;
    }
    try {
      const reply = await postJson("/api/manga-sites/token", { site: li.dataset.site, text });
      show(reply.signin);
      say(li.querySelector(".tool-actions"), "Токен сохранён" + (reply.until ? `, до ${day(reply.until)}` : ""));
    } catch (err) {
      say(li.querySelector(".tool-actions"), err.message, true);
    }
  };

  // ── (v5.44.100) the browser's sign-in: the extension ──
  actions["ms-open"] = async (link) => {
    const li = link.closest("li.ms-site");
    const status = li.querySelector(".tool-actions");
    if (!browser.connected) {                    // no extension: the usual way (the program's window — the usual browser)
      window.open(link.href, "_blank", "noopener");
      say(status, "Сайт открыт в браузере. Войдите на нём — с расширением вход придёт сюда сам.");
      return;
    }
    try {
      const reply = await postJson("/api/manga-sites/open", { site: li.dataset.site });
      say(status, reply.queued ? "Открываю в браузере с расширением — войдите там, вход придёт сюда сам." :
        "Расширение не ответило — откройте сайт ссылкой ещё раз.", !reply.queued);
      if (!reply.queued) refresh(true);
    } catch (err) {
      say(status, err.message, true);
    }
  };

  actions["ms-refresh"] = async (button) => {
    const li = button.closest("li.ms-site");
    const status = li.querySelector(".tool-actions");
    button.disabled = true;
    try {
      const reply = await postJson("/api/manga-sites/refresh", { site: li.dataset.site });
      say(status, "Расширение берёт вход из браузера…");
      const until = Date.now() + 25000;
      while (Date.now() < until) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const st = await refresh(true);
        const told = st && (st.asked || {})[String(reply.id)];
        if (!told) continue;
        if (told.cookies || told.token) {
          say(status, `Вход обновлён: cookies ${told.cookies}` + (told.token ? ", токен" : "") +
            (told.missing && told.missing.length ? ` (не разрешено: ${told.missing.join(", ")})` : ""));
        } else if (told.missing && told.missing.length) {
          say(status, "Расширению не разрешён этот сайт — значок расширения → «Ещё» → «Разрешить сайты манги»", true);
        } else {
          say(status, "В браузере нет входа на этот сайт — откройте его («Открыть сайт») и войдите", true);
        }
        return;
      }
      say(status, "Расширение не ответило — браузер закрыт или расширение выключено", true);
    } catch (err) {
      say(status, err.message, true);
    } finally {
      button.disabled = false;
    }
  };

  actions["ms-forget"] = async (button) => {
    const li = button.closest("li.ms-site");
    if (!confirmed(button, "Точно убрать?")) return;
    try {
      const reply = await postJson("/api/manga-sites/forget", { site: li.dataset.site, what: button.dataset.what });
      show(reply.signin);
      say(li.querySelector(".tool-actions"), button.dataset.what === "token" ? "Токен убран" : "Cookies убраны");
    } catch (err) {
      say(li.querySelector(".tool-actions"), err.message, true);
    }
  };

  refresh();
})();
