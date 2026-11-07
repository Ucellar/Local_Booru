// Local Booru page «Дубли»: the search for look-alike pictures (a page task, its journal polled from
// the program) and the decisions on the pairs: keep one (the other to the trash), «Альтернативная
// версия», «Не дубль», «Пропустить».
(() => {
  "use strict";

  const LB = window.LocalBooru;
  const section = document.getElementById("dupes");
  if (!LB || !section || !LB.watchTask) return;
  const { actions, postJson, say } = LB;
  const confirmed = LB.confirmed || (() => true);
  const form = section.querySelector('form[data-form="dupes-scan"]');

  // ── the search ─────────────────────────────────────────────────────────
  const search = LB.watchTask(section.querySelector("[data-job]"), "/api/dupes/state", {
    onState(st, busy) {
      form.querySelector("[data-scan]").disabled = busy;
      section.querySelector('[data-action="dupes-stop"]').disabled = !(st.status === "running" || st.status === "starting");
    },
    onEnd(st) {
      if (st.status !== "done") return;
      say(form.querySelector("[data-scan]"), "Готово — список обновлён");
      setTimeout(() => location.reload(), 700);
    },
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("[data-scan]");
    button.disabled = true;
    try {
      await postJson("/api/dupes/scan", { threshold: Number(form.elements.namedItem("threshold").value) });
      say(button, "Ищу…");
      search.started();
    } catch (err) {
      say(button, err.message, true);
      button.disabled = false;
    }
  });

  // ── the pairs ──────────────────────────────────────────────────────────
  function planOf(pair) {
    const keep = pair.querySelector('input[type="radio"]:checked');
    if (!keep) return null;
    const ids = pair.dataset.ids.split(" ").map(Number);
    return { keep: Number(keep.value), trash: ids.filter((id) => id !== Number(keep.value)) };
  }

  function dropPairsWith(ids) {
    const gone = new Set(ids.map(String));
    section.querySelectorAll(".dupe").forEach((pair) => {
      if (pair.dataset.ids.split(" ").some((id) => gone.has(id))) pair.remove();
    });
    countLeft();
  }

  function countLeft() {
    const left = section.querySelectorAll(".dupe").length;
    const bulk = section.querySelector(".dupes-bulk");
    if (bulk) bulk.hidden = left === 0;
    if (!left && !section.querySelector(".dupes-done")) {
      const p = document.createElement("p");
      p.className = "hint dupes-done";
      p.textContent = section.querySelector(".dupes-more") ? "Эти пары разобраны — «Показать следующие»." : "Все пары разобраны.";
      section.querySelector(".dupes-list").appendChild(p);
    }
  }

  async function resolve(el, plans) {
    el.disabled = true;
    try {
      const r = await postJson("/api/dupes/resolve", { plans });
      say(el, `В корзину: ${r.trashed}` + (r.errors ? `, ошибок: ${r.errors}` : ""), !!r.errors);
      dropPairsWith(r.gone || []);
    } catch (err) {
      say(el, err.message, true);
    } finally {
      el.disabled = false;
    }
  }

  Object.assign(actions, {
    async "dupes-stop"(el) {
      try { await postJson("/api/dupes/stop"); search.poll(); } catch (err) { say(el, err.message, true); }
    },
    "dupe-resolve"(el) {
      const pair = el.closest(".dupe");
      const plan = planOf(pair);
      if (!plan) { say(el, "Отметьте, какую оставить", true); return; }
      resolve(el, [plan]);
    },
    "dupe-resolve-all"(el) {
      const plans = Array.from(section.querySelectorAll(".dupe")).map(planOf).filter(Boolean);
      if (!plans.length) return;
      if (!confirmed(el, `Разобрать все ${plans.length}: отмеченные оставить, вторые — в корзину? Нажмите ещё раз`)) return;
      resolve(el, plans);
    },
    async "dupe-relation"(el) {
      const pair = el.closest(".dupe");
      try {
        const r = await postJson("/api/dupes/relation", { ids: pair.dataset.ids.split(" ").map(Number), relation: el.dataset.relationKind });
        // (v5.44.116) «Альтернативная версия» — the two cards in one сборка: said for a moment before the pair goes
        const said = r.set_error ? "Запомнено, но в сборку не собрано: " + r.set_error
          : r.set ? "Запомнено; обе в одной сборке" + (r.cards ? ` (карточек в ней: ${r.cards})` : "") : "";
        if (!said) {
          pair.remove();
          countLeft();
          return;
        }
        say(el, said, !!r.set_error);
        el.closest(".tool-actions").querySelectorAll("button").forEach((b) => { b.disabled = true; });
        setTimeout(() => { pair.remove(); countLeft(); }, r.set_error ? 5000 : 1500);
      } catch (err) {
        say(el, err.message, true);
      }
    },
    "dupe-skip"(el) {
      el.closest(".dupe").remove();
      countLeft();
    },
  });

  // a picked picture is marked in its pair
  section.addEventListener("change", (event) => {
    const radio = event.target.closest('.dupe input[type="radio"]');
    if (!radio) return;
    radio.closest(".dupe").querySelectorAll(".dupe-item").forEach((fig) => {
      fig.classList.toggle("keep", fig.contains(radio));
    });
  });
  section.querySelectorAll('.dupe input[type="radio"]:checked').forEach((radio) => radio.closest(".dupe-item").classList.add("keep"));
})();
