// (v5.44.78) «Настройки → Сайты манги» (webui/pages/manga_sites.py, core/manhwa_access.py): the addresses of the sites
// of manga and manhwa (a site that moved) and the owner's sign-in — cookies from a file read here and sent to the
// program, MangaLib's token pasted.  What comes back is only what there is (how many cookies, until when the token
// lasts): no value is ever shown; the token's field is emptied as soon as it is sent.
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
      const state = li.querySelector("[data-ms-state]");
      state.textContent = parts.length ? "вход: " + parts.join("; ") : "вход не задан";
      state.classList.toggle("on", parts.length > 0);
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
      if (!quiet) say(form.querySelector('button[type="submit"]'), "Не узнать, что сохранено: " + err.message, true);
      return null;
    }
  }

  // (v5.44.100) what the extension brings while the page is open (the owner signs in on a site it opened)
  setInterval(() => { if (!document.hidden && document.body.contains(box)) refresh(true); }, 5000);

  // ── (v5.44.79) the language of MangaDex's chapters ──
  const languageForm = box.querySelector("[data-ms-language]");
  if (languageForm) {
    languageForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = languageForm.querySelector('button[type="submit"]');
      try {
        await postJson("/api/manga-sites/language", { language: languageForm.elements.language.value });
        say(button, "Язык сохранён");
      } catch (err) {
        say(button, err.message, true);
      }
    });
  }

  // ── the addresses ──
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const asked = {};
    form.querySelectorAll("input[name]").forEach((input) => {
      input.removeAttribute("aria-invalid");
      asked[input.name] = input.value.trim();
    });
    form.querySelectorAll(".ms-wrong").forEach((n) => n.remove());
    try {
      const reply = await postJson("/api/manga-sites/addresses", { addresses: asked });
      (reply.sites || []).forEach((site) => {
        const field = form.querySelector(`input[name="${site.name}"]`);
        if (field) field.value = site.mine || "";
        const api = form.querySelector(`input[name="${site.name}_api"]`);
        if (api && site.api) api.value = site.api.mine || "";
      });
      say(button, "Адреса сохранены");
    } catch (err) {
      const wrong = (err.data && err.data.wrong) || {};
      Object.keys(wrong).forEach((name) => {
        const field = form.querySelector(`input[name="${name}"]`);
        if (!field) return;
        field.setAttribute("aria-invalid", "true");
        const note = document.createElement("small");
        note.className = "ms-wrong";
        note.textContent = wrong[name];
        field.parentElement.append(note);
      });
      say(button, err.message, true);
    }
  });

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
