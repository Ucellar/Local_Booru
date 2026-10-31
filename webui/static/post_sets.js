// The сборка over a card's picture (webui/pages/post_sets.py draws it): «« скрыть / показать »» (the program
// remembers it for every window), «изменить» — ‹ › move a card, × take it out of the сборка, its name, «Разобрать
// сборку», «+ добавить карточки» (the gallery, to choose them).  A change is written at once (/api/sets/…).
// (v5.30; a series and a work v5.32) The parts of a сборка the library has not come from the site (data-remote — the
// program asks it, seconds the first time: «ищу на сайте…» meanwhile) and go in among the cards with «api»; the page
// of such a part (/post/remote) has «Скачать» — the post goes to the program's import, its state is asked until it
// is done.
// (v5.44.1) A card of no сборка whose post has a family on a site: the same box (data-lone, nothing to change in it),
// shown only when the site has parts the library has not.
(() => {
  "use strict";
  const box = document.querySelector(".post-sets[data-set]");
  const LB = window.LocalBooru;
  if (!box || !LB) return;
  const setId = Number(box.dataset.set);
  const postId = Number(box.dataset.post);
  const toggle = box.querySelector(".post-set-toggle");
  const edit = box.querySelector(".post-set-edit");          // none in a lone card's box
  const tools = box.querySelector(".post-set-tools");
  const strip = box.querySelector(".post-set-thumbs");
  const lone = box.dataset.lone === "1";
  const EDITING = "lb_set_editing";             // «изменить» stays on over the pages of this window

  const relayout = () => window.dispatchEvent(new Event("resize"));   // the picture under the box is fitted again

  function setHidden(hide) {
    box.classList.toggle("collapsed", hide);
    toggle.textContent = hide ? "показать »" : "« скрыть";
    toggle.setAttribute("aria-expanded", hide ? "false" : "true");
    relayout();
  }

  function setEditing(on) {
    if (!edit || !tools) return;
    box.classList.toggle("editing", on);
    edit.setAttribute("aria-pressed", on ? "true" : "false");
    edit.textContent = on ? "готово" : "изменить";
    tools.hidden = !on;
    try { sessionStorage.setItem(EDITING, on ? String(setId) : ""); } catch (_) { /* not kept: a private window */ }
    if (on && box.classList.contains("collapsed")) setHidden(false);
    relayout();
  }

  toggle.addEventListener("click", () => {
    const hide = !box.classList.contains("collapsed");
    if (hide && box.classList.contains("editing")) setEditing(false);
    setHidden(hide);
    LB.postJson("/api/prefs", { sets_box: hide ? "hidden" : "shown" }).catch(() => {});
  });
  if (edit) {
    edit.addEventListener("click", () => setEditing(!box.classList.contains("editing")));
    try { if (sessionStorage.getItem(EDITING) === String(setId)) setEditing(true); } catch (_) { /* nothing kept */ }
  }

  function status(text, isError) {
    const line = tools && tools.querySelector(".action-status");
    if (!line) return;
    line.textContent = text;
    line.style.color = isError ? "var(--danger)" : "";
  }

  // «k из N»: where this card (or the part whose page this is) is now — among the parts from the site too
  function place() {
    const items = [...strip.querySelectorAll(".post-set-item")];
    const at = items.findIndex((it) => it.classList.contains("current"));
    const line = box.querySelector(".post-set-place");
    if (line && at >= 0) line.textContent = " · " + (at + 1) + " из " + items.length;
  }

  // the parts the library has not, from the site
  async function remoteParts() {
    const address = box.dataset.remote;
    if (!address) return;
    const current = box.dataset.remoteCurrent || "";
    const note = box.querySelector(".post-set-missing");
    const asking = setTimeout(() => {           // the first time the site is asked: seconds
      if (!note) return;
      note.textContent = " · ищу недостающие на сайте…";
      note.title = "Программа спрашивает сайт, каких частей этой сборки нет в библиотеке";
      note.hidden = false;
    }, 400);
    let data;
    try {
      const response = await fetch(address + (address.includes("?") ? "&" : "?") + "current=" + encodeURIComponent(current),
                                   { credentials: "same-origin" });
      data = await response.json();
    } catch (_) {
      data = null;                              // no parts then: the сборка itself is all there
    }
    clearTimeout(asking);
    if (note) {
      note.hidden = true;
      note.textContent = "";
    }
    if (!data || !data.ok) return;
    if (lone && (data.tiles || []).length) box.hidden = false;      // the family the site has: now there is a box
    for (const tile of data.tiles || []) {
      if (strip.querySelector(`.post-set-item[data-remote="${CSS.escape(tile.key)}"]`)) continue;
      const holder = document.createElement("div");
      holder.innerHTML = tile.html;
      const item = holder.firstElementChild;
      if (!item) continue;
      const before = tile.before ? strip.querySelector(`.post-set-item[data-id="${Number(tile.before)}"]`) : null;
      strip.insertBefore(item, before);
    }
    if (note && data.missing) {
      note.textContent = " · нет в библиотеке: " + data.missing + " (api)";
      note.title = "Части этой сборки, которых нет в библиотеке, — превью с сайта " + (data.site || "") + " через его API";
      note.hidden = false;
    } else if (note && data.note) {
      note.textContent = "";
      note.title = data.note;
    }
    place();
    relayout();
  }
  remoteParts();

  async function act(button) {
    const what = button.dataset.setAct;
    const item = button.closest(".post-set-item");
    if (what === "left" || what === "right") {
      const items = [...strip.querySelectorAll(".post-set-item:not(.remote)")];      // the parts from the site stay
      const at = items.indexOf(item);
      const to = what === "left" ? at - 1 : at + 1;
      if (to < 0 || to >= items.length) return;
      await LB.postJson("/api/sets/move", { set: setId, id: Number(item.dataset.id), to });
      if (what === "left") strip.insertBefore(item, items[to]);
      else strip.insertBefore(items[to], item);
      place();
      button.focus();
      status("Порядок сохранён");
    } else if (what === "remove") {
      await LB.postJson("/api/sets/remove", { ids: [Number(item.dataset.id)] });
      location.reload();                        // the headline and the count are the server's
    } else if (what === "rename") {
      const input = tools.querySelector('input[name="title"]');
      await LB.postJson("/api/sets/rename", { set: setId, title: input.value });
      location.reload();
    } else if (what === "dissolve") {
      if (!LB.confirmed || !LB.confirmed(button, "Разобрать сборку? Нажмите ещё раз")) return;
      await LB.postJson("/api/sets/dissolve", { sets: [setId] });
      try { sessionStorage.removeItem(EDITING); } catch (_) { /* nothing kept */ }
      location.reload();
    }
  }

  box.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-set-act]");
    if (!button || !box.contains(button) || button.disabled) return;
    event.preventDefault();
    button.disabled = true;
    try {
      await act(button);
    } catch (err) {
      status(err.message, true);
    } finally {
      button.disabled = false;
    }
  });
  const title = tools && tools.querySelector('input[name="title"]');
  if (title) {
    title.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        tools.querySelector('[data-set-act="rename"]').click();
      }
    });
  }
})();

// «Скачать» on the page of a part the library has not (/post/remote): into the library through the program's import —
// its state asked every second until it is done (then — a link to the card it became)
(() => {
  "use strict";
  const button = document.querySelector(".remote-download");
  const LB = window.LocalBooru;
  if (!button || !LB) return;
  const bar = button.closest(".remote-bar");
  const line = bar.querySelector(".action-status");

  function say(text, isError) {
    line.textContent = text;
    line.style.color = isError ? "var(--danger)" : "";
  }

  function done(imageId) {
    button.hidden = true;
    line.textContent = "";
    const link = document.createElement("a");
    link.href = "/post/" + Number(imageId);
    link.textContent = "Скачано — открыть карточку";
    line.appendChild(link);
  }

  async function follow(task) {
    const started = Date.now();
    while (Date.now() - started < 30 * 60 * 1000) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      let state;
      try {
        const response = await fetch("/api/sets/remote/task?id=" + encodeURIComponent(task), { credentials: "same-origin" });
        state = await response.json();
      } catch (_) {
        continue;                               // the program busy for a moment: ask again
      }
      if (!state.ok) {
        say(state.error || "Загрузка потерялась", true);
        button.disabled = false;
        return;
      }
      if (state.status === "done") {
        if (state.image_id) done(state.image_id);
        else say("Готово" + (state.message ? ": " + state.message : ""));
        return;
      }
      if (state.status === "failed" || state.status === "cancelled" || state.status === "deferred") {
        say(state.words + (state.message ? ": " + state.message : ""), true);
        button.disabled = false;
        return;
      }
      say(state.words[0].toUpperCase() + state.words.slice(1) + "…");
    }
    say("Скачивание идёт слишком долго — посмотрите позже в галерее", true);
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    say("Отправляю…");
    try {
      const answer = await LB.postJson("/api/sets/remote/download", { f: button.dataset.f, k: button.dataset.k });
      if (answer.done) done(answer.image_id);
      else follow(answer.task);
    } catch (err) {
      say(err.message, true);
      button.disabled = false;
    }
  });
})();
