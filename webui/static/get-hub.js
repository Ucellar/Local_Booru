// Local Booru «Скачать» (v5.44.75, webui/pages/get_hub.py): a link put into a part that is not its own goes to its
// own — a video's link in «Манхва» to «Видео», a booru post's to «Галерея», a gallery of nhentai to «Манга».  The field's
// own search (its form's «submit», a paste included: the pages submit a pasted link) is held while the program says
// where the link belongs (/api/get/route), then let through — or the page of that part opens with the link.
(() => {
  "use strict";
  const LB = window.LocalBooru || {};
  const part = document.body.dataset.hub;
  if (!part || !LB.postJson) return;
  const say = LB.say || (() => {});
  // the fields of the parts: (the field's name for the program, its form, the field in it)
  const FIELDS = [
    ["gallery", '#grab form[data-form="grab-search"]', 'input[name="q"]'],
    ["site", '#manhwa-get form[data-form="manhwa-find"]', 'input[name="url"]'],
    ["nhentai", '#manga-get form[data-form="manga-get"]', 'textarea[name="text"]'],
    ["video", '#vid-get form[data-form="vid-find-url"]', 'input[name="url"]'],
  ];
  const ONE_LINK = /^https?:\/\/\S+$/i;
  let passing = null;              // the form let through: its own handler takes this submit

  function entryOf(form) {
    return FIELDS.find(([, selector]) => form.matches && form.matches(selector)) || null;
  }

  function pass(form) {
    passing = form;
    try { form.requestSubmit(); } finally { passing = null; }
  }

  function into(name, text) {          // the other field of this page («Манга»: its sites and nhentai)
    const entry = FIELDS.find(([key]) => key === name);
    const form = entry && document.querySelector(entry[1]);
    const field = form && form.querySelector(entry[2]);
    if (!field) return false;
    field.value = text;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    form.scrollIntoView({ block: "nearest", behavior: "smooth" });
    field.focus({ preventScroll: true });
    if (name !== "nhentai") pass(form);   // a gallery of nhentai is downloaded by its «Скачать», not at once
    return true;
  }

  async function route(form, name, field, text) {
    let answer = { stay: true };
    try {
      answer = await LB.postJson("/api/get/route", { text, field: name, part });
    } catch (err) {
      answer = { stay: true };          // the program did not say: the field does as it did
    }
    if (answer.go) {
      say(form, "Это ссылка другого раздела — открываю его…");
      location.assign(answer.go);
      return;
    }
    if (answer.move) {                   // the field it was put into is emptied: the link is in its own now
      field.value = "";
      field.dispatchEvent(new Event("input", { bubbles: true }));
      if (into(answer.move, text)) return;
      field.value = text;
    }
    if (answer.error) {
      say(form, answer.error, true);
      return;
    }
    pass(form);
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form === passing) return;
    const entry = entryOf(form);
    if (!entry) return;
    const field = form.querySelector(entry[2]);
    const text = (field && field.value || "").trim();
    if (!ONE_LINK.test(text)) return;     // words, tags, numbers: the field's own
    event.preventDefault();
    event.stopImmediatePropagation();
    route(form, entry[0], field, text);
  }, true);
})();
