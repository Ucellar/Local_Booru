// (v5.44.71) «Настройки → Телефон» → «Через ваш сервер» (webui/pages/phone_relay.py): the server's address, login
// and password → «Подключить сервер» (a task of the program: its journal here; the password field is emptied at once),
// the connection, «Подключить телефон» — the QR code and the eight digits (a new code comes by itself when the old one is
// used or old), the phones paired, «Проверить», «Выключить» / «Включить», «Настроить заново», «Убрать с сервера»,
// «Забыть сервер».
(() => {
  "use strict";
  const LB = window.LocalBooru;
  const box = document.getElementById("phone-relay");
  if (!LB || !box) return;
  const { actions, postJson, say } = LB;
  const status = box.querySelector("[data-relay-status]");
  const form = box.querySelector("[data-relay-form]");
  const goButton = form.querySelector("[data-relay-go]");
  const cancelButton = form.querySelector('[data-action="relay-cancel"]');
  const formHint = form.querySelector("[data-relay-form-hint]");
  const hostRows = form.querySelectorAll("[data-relay-host-row]");
  const run = box.querySelector("[data-relay-run]");
  const log = run.querySelector(".job-log");
  const onBox = box.querySelector("[data-relay-on-box]");
  const pairBox = box.querySelector("[data-relay-pair]");
  const qr = box.querySelector("[data-relay-qr]");
  const devicesBox = box.querySelector("[data-relay-devices]");
  const SETUP_HINT = formHint.textContent;
  const RUNNING = ["starting", "running", "stopping"];
  let st = null;
  let mode = "";                     // the form: "setup" (anew) | "remove"; "" — not asked for
  let task = { id: 0, after: 0, on: false };
  let shownCode = "";
  let timer = 0;

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const linkTo = (href) => {
    const a = el("a", "", href);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    return a;
  };
  const when = (seconds) => {
    if (!seconds) return "";
    const d = new Date(seconds * 1000);
    const day = d.toDateString() === new Date().toDateString() ? "" : d.toLocaleDateString("ru-RU") + " ";
    return day + d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  };
  const LINK = { up: "Связь с сервером есть", connecting: "Соединяюсь с сервером…", down: "Нет связи с сервером" };

  function render() {
    if (!st) return;
    const link = st.link || {};
    status.textContent = "";
    if (!st.available) {
      status.append(el("p", "tool-warn", "В программе нет модуля paramiko — он ставится вместе с обновлением программы."));
    }
    if (!st.configured) {
      status.append(el("p", "", "Сервер ещё не подключён."));
    } else if (!st.on) {
      status.append(el("p", "tool-warn", "Связь через сервер выключена — телефон не войдёт, пока не нажмёте «Включить»."));
    } else {
      const line = el("p", link.state === "up" ? "relay-up" : "tool-warn", LINK[link.state] || "");
      if (link.state === "up") {
        line.append(". Адрес для телефона: ", linkTo(st.address));
      }
      status.append(line);
      if (link.state !== "up" && link.why) status.append(el("p", "hint", link.why));
    }
    const busy = task.on;
    const asking = !st.configured || !!mode;
    form.hidden = busy || !asking;
    const removing = mode === "remove";
    hostRows.forEach((row) => { row.hidden = removing; });
    goButton.textContent = removing ? "Убрать с сервера" : st.configured ? "Настроить заново" : "Подключить сервер";
    goButton.classList.toggle("danger", removing);
    formHint.textContent = removing ? "Программа войдёт на сервер ещё раз (пароль снова не сохраняется) и уберёт всё своё: "
      + "веб-сервер, свой вход и открытые ею порты. VPN не трогается." : SETUP_HINT;
    cancelButton.hidden = !st.configured;
    onBox.hidden = !st.configured || asking || busy;
    if (!onBox.hidden) {
      pairBox.hidden = !st.on;
      if (st.on && st.code) {
        const address = box.querySelector("[data-relay-address]");
        address.href = st.address;
        address.textContent = st.address;
        box.querySelector("[data-relay-code]").textContent = st.code.short;
        box.querySelector("[data-relay-until]").textContent = "Код действует до " + when(st.code.until) + ".";
        if (st.code.short !== shownCode) {
          shownCode = st.code.short;
          qr.src = "/api/phone/pair.svg?t=" + Date.now();
        }
      }
      box.querySelector('[data-action="relay-switch"][data-on=""]').hidden = !st.on;
      box.querySelector('[data-action="relay-switch"][data-on="1"]').hidden = st.on;
      box.querySelector('[data-action="relay-check"]').hidden = !st.on;
      devicesBox.textContent = "";
      for (const d of st.devices || []) {
        const li = el("li");
        const text = el("span", "phone-device-text");
        text.append(el("b", "", d.name || "Телефон"), el("span", "hint", "подключён " + when(d.at)
          + (d.seen && d.seen - d.at > 60 ? " · последний раз " + when(d.seen) : "")));
        const off = el("button", "", "Отключить");
        off.type = "button";
        off.dataset.action = "relay-device";
        off.dataset.id = d.id;
        li.append(text, off);
        devicesBox.append(li);
      }
      if (!devicesBox.children.length) devicesBox.append(el("li", "hint", "Пока ни одного."));
    }
  }

  async function load() {
    try {
      const r = await fetch("/api/phone/relay/state?code=1", { credentials: "same-origin" });
      st = await r.json();
    } catch (err) {
      status.textContent = "";
      status.append(el("p", "tool-warn", "Программа не ответила: " + err.message));
      return;
    }
    if (st.task && RUNNING.includes(st.task.status) && !task.on) watchTask(st.task.id);
    render();
  }

  // the task of the setup or the removal: its journal, then what came of it
  function watchTask(id) {
    if (task.on && task.id === id) return;
    task = { id, after: 0, on: true };
    log.textContent = "";
    run.hidden = false;
    run.querySelectorAll(".relay-result").forEach((n) => n.remove());
    render();
    poll();
  }

  async function poll() {
    let s;
    try {
      const r = await fetch("/api/phone/relay/task?after=" + task.after, { credentials: "same-origin" });
      s = await r.json();
    } catch (_) {
      setTimeout(poll, 2000);
      return;
    }
    if (s.id !== task.id) { setTimeout(poll, 1000); return; }
    for (const line of s.log || []) log.textContent += line.text + "\n";
    if (s.last_seq) task.after = s.last_seq;
    log.scrollTop = log.scrollHeight;
    if (RUNNING.includes(s.status)) { setTimeout(poll, 1000); return; }
    task.on = false;
    const result = s.result || {};
    const end = el("p", "relay-result " + (s.status === "done" && !result.warn ? "relay-up" : "tool-warn"),
      s.status === "failed" ? (result.failed || s.error || "Не получилось — причина в журнале выше")
        : s.status === "stopped" ? "Остановлено"
          : result.warn ? "Почти готово: " + result.warn
            : result.address ? "Готово: телефон открывает " + result.address + " — подключите его кодом ниже" : "Готово");
    run.append(end);
    mode = s.status === "failed" ? mode : "";
    delete box.dataset.redo;
    await load();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const removing = mode === "remove";
    const data = { host: form.host.value.trim(), port: Number(form.port.value) || 22,
      login: form.login.value.trim() || "root", password: form.password.value };
    if (!removing && !data.host) { say(goButton, "Введите адрес сервера", true); form.host.focus(); return; }
    if (!data.password) { say(goButton, "Введите пароль сервера", true); form.password.focus(); return; }
    goButton.disabled = true;
    say(goButton, removing ? "Убираю…" : "Начинаю…");
    try {
      const r = await postJson(removing ? "/api/phone/relay/remove" : "/api/phone/relay/setup", data);
      say(goButton, "");
      watchTask(r.id);
    } catch (err) {
      say(goButton, err.message, true);
    } finally {
      form.password.value = "";                  // the password is not kept on the page either
      goButton.disabled = false;
    }
  });

  const acting = (button, text) => {
    const status = button.closest(".tool-actions").querySelector(".action-status");
    return (message, bad) => say(status || button, message === undefined ? text : message, bad);
  };

  actions["relay-new-code"] = async (button) => {
    const tell = acting(button);
    try {
      await postJson("/api/phone/pair/new", {});
      await load();
      tell("Новый код на экране");
    } catch (err) { tell(err.message, true); }
  };
  actions["relay-check"] = async (button) => {
    const tell = acting(button);
    button.disabled = true;
    tell("Проверяю путь телефона: этот компьютер → интернет → сервер → программа…");
    try {
      const r = await postJson("/api/phone/relay/check", {});
      tell(r.message || "Связь есть");
    } catch (err) { tell(err.message, true); } finally { button.disabled = false; }
  };
  actions["relay-switch"] = async (button) => {
    const tell = acting(button);
    try {
      const r = await postJson("/api/phone/relay/switch", { on: !!button.dataset.on });
      tell(r.message || "Готово");
      await load();
    } catch (err) { tell(err.message, true); }
  };
  actions["relay-redo"] = () => {
    mode = "setup";
    box.dataset.redo = "1";
    render();
    form.password.focus();
  };
  actions["relay-remove"] = () => {
    mode = "remove";
    render();
    form.password.focus();
  };
  actions["relay-cancel"] = () => {
    mode = "";
    delete box.dataset.redo;
    render();
  };
  actions["relay-forget"] = async (button) => {
    const tell = acting(button);
    if (button.dataset.sure !== "1") {             // the second press does it: no dialog in the program's window
      button.dataset.sure = "1";
      button.textContent = "Точно забыть сервер?";
      setTimeout(() => { button.dataset.sure = ""; button.textContent = "Забыть сервер"; }, 5000);
      return;
    }
    button.dataset.sure = "";
    button.textContent = "Забыть сервер";
    try {
      const r = await postJson("/api/phone/relay/forget", {});
      tell(r.message || "Готово");
      await load();
    } catch (err) { tell(err.message, true); }
  };
  actions["relay-device"] = async (button) => {
    button.disabled = true;
    try {
      await postJson("/api/phone/devices/remove", { id: button.dataset.id });
      await load();
    } catch (err) {
      say(button, err.message, true);
      button.disabled = false;
    }
  };

  // asked when the tab «Телефон» is shown, then every few seconds while it is (the connection, a phone paired, a
  // new code)
  const tick = () => {
    clearTimeout(timer);
    if (!document.hidden && box.offsetParent !== null) load();
    timer = setTimeout(tick, 5000);
  };
  if ("IntersectionObserver" in window) {
    const seen = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      seen.disconnect();
      tick();
    });
    seen.observe(box);
  } else {
    tick();
  }
})();
