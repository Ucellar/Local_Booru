// Local Booru pages «Диагностика», «Обслуживание» and «Удаление и сброс»: the check of the library, the
// repairs, the maintenance jobs and the deletions — page tasks of one kind, one at a time; their journal is
// polled from the program, and the page reloads with the new report (or the job's result) when the task ends.
(() => {
  "use strict";

  const LB = window.LocalBooru;
  const section = document.getElementById("diag");
  if (!LB || !section || !LB.watchTask) return;
  const { actions, postJson, say } = LB;
  const confirmed = LB.confirmed || (() => true);
  const form = section.querySelector('form[data-form="diag-audit"]');
  const job = section.querySelector("[data-job]");
  const stops = section.querySelectorAll('[data-action="diag-stop"]');   // (v5.44.118) one by each start button
  const stop = stops[0] || null;
  const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let pressed = null;                 // the button that started the task: its line says how it ended

  const task = LB.watchTask(job, "/api/diag/state", {
    onState(st, busy) {
      section.querySelectorAll("[data-needs-idle]").forEach((b) => { b.disabled = busy; });
      // work that has no place to stop half-way keeps «Остановить» off
      const going = (st.status === "running" || st.status === "starting") && st.stoppable !== false;
      stops.forEach((b) => { b.disabled = !going; });
    },
    onEnd(st) {
      const where = pressed || (form && form.querySelector("[data-audit]")) || stop;
      say(where, st.status === "failed" ? "Прервано ошибкой — см. журнал" : "Готово — обновляю");
      setTimeout(() => location.reload(), st.status === "failed" ? 2500 : 900);
    },
  });

  function showJournal() {
    job.hidden = false;
    job.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "nearest" });
  }

  async function begin(button, url, body, saying) {
    button.disabled = true;
    try {
      await postJson(url, body);
      pressed = button;
      say(button, saying);
      task.started();
      showJournal();
    } catch (err) {
      say(button, err.message, true);
      button.disabled = false;
    }
  }

  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const files = form.elements.namedItem("verify_files");     // (v5.44.118) the files — «Проверить файлы»
      begin(form.querySelector("[data-audit]"), "/api/diag/audit",
            { verify_files: !!(files && files.checked) }, "Проверяю…");
    });
  }

  // (v5.44.118) «Файлы библиотеки»: one check of the files (with «и сверить каждый файл по MD5»)
  const filesForm = section.querySelector('form[data-form="files-check"]');
  if (filesForm) {
    filesForm.addEventListener("submit", (event) => {
      event.preventDefault();
      begin(filesForm.querySelector("[data-files-check]"), "/api/diag/files/check",
            { md5: filesForm.elements.namedItem("md5").checked }, "Проверяю файлы — журнал вверху");
    });
  }

  const sources = section.querySelector('form[data-form="diag-sources"]');
  if (sources) {
    sources.addEventListener("submit", (event) => {
      event.preventDefault();
      begin(sources.querySelector('button[type="submit"]'), "/api/diag/sources", {
        verify_network: sources.elements.namedItem("verify_network").checked,
        limit: Number(sources.elements.namedItem("limit").value || 0),
      }, "Ищу — журнал вверху страницы; найденное появится здесь");
    });
  }

  // «Обслуживание»: each job is a form with its button and its switches
  section.querySelectorAll('form[data-form="maint"]').forEach((jobForm) => {
    jobForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = jobForm.querySelector('button[type="submit"]');
      if (button.dataset.ask && !confirmed(button, button.dataset.ask)) return;
      const options = {};
      jobForm.querySelectorAll('input[type="checkbox"]').forEach((box) => { options[box.name] = box.checked; });
      begin(button, "/api/diag/maintain", { action: jobForm.dataset.job, options }, "Идёт — журнал вверху");
    });
  });

  // «Удаление и сброс»: a choice is shown first (how many cards, how big, the first of them); its own button
  // moves what was shown to the trash.  Any change of the choice hides the old answer.
  section.querySelectorAll('form[data-form="reset-find"]').forEach((findForm) => {
    const box = findForm.querySelector("[data-preview]");
    const kind = findForm.elements.namedItem("kind");          // tag or source (the first form only)
    const field = (name) => findForm.elements.namedItem(name);
    let asked = 0;                     // an answer to an older question (the choice changed since) is dropped
    const hide = () => { asked += 1; box.hidden = true; box.textContent = ""; };
    if (kind) {
      const sync = () => {
        findForm.querySelectorAll(".reset-value").forEach((label) => {
          const on = label.dataset.for === kind.value;
          label.hidden = !on;
          label.querySelector("input").disabled = !on;
        });
      };
      kind.addEventListener("change", sync);
      sync();
    }
    findForm.addEventListener("input", hide);        // typing and a new pick in a list («change» of a field
                                                     // comes on Enter too: it would drop the answer asked for)
    const source = field("source");
    if (source) {
      source.addEventListener("focus", async () => {          // the sites of the library, most cards first
        const list = document.getElementById(source.getAttribute("list"));
        if (!list || list.dataset.loaded) return;
        list.dataset.loaded = "1";
        try {
          const r = await fetch("/api/reset/hosts", { credentials: "same-origin" });
          const data = await r.json();
          (data.items || []).forEach((item) => {
            const option = document.createElement("option");
            option.value = item.host;
            option.textContent = item.label;
            list.appendChild(option);
          });
        } catch (err) {
          delete list.dataset.loaded;
        }
      });
    }
    findForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = findForm.querySelector('button[type="submit"]');
      findForm.querySelectorAll(".ac-list").forEach((list) => { list.hidden = true; });   // Enter without a pick
      const body = kind
        ? { what: kind.value, value: field(kind.value).value, scope: field("scope").value }
        : { what: findForm.dataset.what, value: field("mode").value };
      button.disabled = true;
      say(button, "Ищу…");
      const mine = ++asked;
      try {
        const answer = await postJson("/api/reset/preview", body);
        if (mine !== asked) return;
        box.innerHTML = answer.html;         // built by the program, every name in it escaped there
        box.hidden = false;
        say(button, "");
      } catch (err) {
        if (mine !== asked) return;
        box.hidden = true;                   // not hide(): that would make this very answer «old»
        box.textContent = "";
        say(button, err.message, true);
      } finally {
        button.disabled = false;
        if (mine !== asked) say(button, "");
      }
    });
  });

  const missingForm = section.querySelector('form[data-form="reset-missing"]');
  if (missingForm) {
    missingForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = missingForm.querySelector('button[type="submit"]');
      if (button.dataset.ask && !confirmed(button, button.dataset.ask)) return;
      begin(button, "/api/reset/missing", {}, "Идёт — журнал вверху");
    });
  }

  // «Удалить всё»: the word DELETE unlocks the button, a second press orders the reset for the next start
  const fullForm = section.querySelector('form[data-form="reset-full"]');
  if (fullForm) {
    const word = fullForm.elements.namedItem("confirm");
    const go = fullForm.querySelector('button[type="submit"]');
    const typed = () => word.value.trim() === "DELETE";
    word.addEventListener("input", () => { go.disabled = !typed(); });
    fullForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!typed() || !confirmed(go, go.dataset.ask)) return;
      go.disabled = true;
      try {
        await postJson("/api/reset/full", { confirm: word.value.trim(),
          subscriptions: fullForm.elements.namedItem("subscriptions").checked });
        say(go, "Заказано — обновляю");
        setTimeout(() => location.reload(), 600);
      } catch (err) {
        say(go, err.message, true);
        go.disabled = !typed();
      }
    });
  }

  Object.assign(actions, {
    async "reset-trash"(el) {
      if (!confirmed(el, el.dataset.ask)) return;
      begin(el, "/api/reset/trash", { token: el.dataset.token }, "Идёт — журнал вверху");
    },
    async "reset-cancel"(el) {
      el.disabled = true;
      try {
        await postJson("/api/reset/cancel");
        say(el, "Сброс отменён — обновляю");
        setTimeout(() => location.reload(), 600);
      } catch (err) {
        say(el, err.message, true);
        el.disabled = false;
      }
    },
    async "diag-stop"(el) {
      try { await postJson("/api/diag/stop"); task.poll(); } catch (err) { say(el, err.message, true); }
    },
    async "diag-repair"(el) {
      if (el.dataset.ask && !confirmed(el, el.dataset.ask)) return;
      begin(el, "/api/diag/repair", { action: el.dataset.repair }, "Идёт — журнал вверху");
    },
    // (v5.44.118) «Починить файлы (N)»: exactly what the check shown on this page found (its stamp)
    async "files-repair"(el) {
      if (el.dataset.ask && !confirmed(el, el.dataset.ask)) return;
      begin(el, "/api/diag/files/repair", { stamp: el.dataset.stamp }, "Чиню — журнал вверху");
    },
  });
})();
