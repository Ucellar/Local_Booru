// Local Booru (v5.44.153) — «Справка» (webui/pages/help.py): the field leaves the sections with the typed words and
// marks the lines that have them; the list of sections says which one is being read; a look whose header stays at
// the top while the page scrolls does not cover the heading a link jumps to.
(() => {
  "use strict";
  const root = document.getElementById("help-page");
  if (!root) return;
  const field = document.getElementById("hp-find");
  const none = document.getElementById("hp-none");
  const sections = Array.from(root.querySelectorAll(".hp-section"));
  const links = new Map(Array.from(root.querySelectorAll(".hp-contents a"))
    .map((a) => [a.getAttribute("href").slice(1), a]));

  // «ё» is typed as «е» as often as not; the case does not matter
  const plain = (text) => String(text || "").toLowerCase().replace(/ё/g, "е");
  const lines = (section) => Array.from(section.querySelectorAll("h2, h3, p, li, tr"));
  const kept = sections.map((section) => ({
    section,
    text: plain(section.textContent),
    lines: lines(section).map((line) => ({ line, text: plain(line.textContent) })),
  }));

  function find() {
    const words = plain(field.value).split(/\s+/).filter(Boolean);
    let shown = 0;
    let first = null;
    for (const item of kept) {
      const fits = words.every((word) => item.text.includes(word));
      item.section.hidden = !fits;
      const link = links.get(item.section.id);
      if (link) link.parentElement.hidden = !fits;
      for (const { line, text } of item.lines) {
        const hit = fits && words.length > 0 && line.tagName !== "H2" && words.some((word) => text.includes(word));
        line.classList.toggle("hp-hit", hit);
        if (hit && !first) first = line;
      }
      if (fits) shown += 1;
    }
    none.hidden = shown > 0;
    return first;
  }

  if (field) {
    let timer = 0;
    field.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(find, 120);
    });
    field.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && field.value) {
        event.preventDefault();
        field.value = "";
        find();
      } else if (event.key === "Enter") {
        event.preventDefault();
        window.clearTimeout(timer);
        const first = find();
        if (first) first.scrollIntoView({ block: "center" });
      }
    });
    if (field.value) find();                  // a page come back to keeps what was typed
  }

  // ── the header of some looks stays at the top: the place a link jumps to is below it ──
  function measure() {
    const header = document.querySelector("header.top");
    let top = 12;
    if (header) {
      const style = window.getComputedStyle(header);
      const box = header.getBoundingClientRect();
      const stays = style.position === "sticky" || style.position === "fixed";
      const across = box.width >= window.innerWidth * 0.6 && box.height < window.innerHeight * 0.4 && box.top <= 1;
      if (stays && across) top = Math.round(box.height) + 12;
    }
    root.style.setProperty("--hp-top", top + "px");
    return top;
  }
  let topLine = measure();
  window.addEventListener("resize", () => { topLine = measure(); spy(); });

  // ── which section is being read ──
  function spy() {
    let current = null;
    for (const section of sections) {
      if (section.hidden) continue;
      if (section.getBoundingClientRect().top - topLine <= 24) current = section;
      else break;
    }
    if (!current) current = sections.find((section) => !section.hidden) || null;
    const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
    if (atEnd) {
      const last = sections.filter((section) => !section.hidden).pop();
      if (last && last.getBoundingClientRect().top < window.innerHeight) current = last;
    }
    for (const [id, link] of links) {
      if (current && id === current.id) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    }
  }
  let waiting = false;
  window.addEventListener("scroll", () => {
    if (waiting) return;
    waiting = true;
    window.requestAnimationFrame(() => { waiting = false; spy(); });
  }, { passive: true });
  if (field) field.addEventListener("input", () => window.setTimeout(spy, 160));

  // the address came with a section: the browser jumped before the room for the header was known
  if (location.hash.length > 1) {
    const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (target && topLine > 12) target.scrollIntoView();
  }
  spy();
  // (v5.44.158) a page coming in is drawn a little lower than it stands (booru.css): looked at again when it is there
  document.addEventListener("animationend", (event) => {
    if (event.target instanceof Element && event.target.matches("main.content")) spy();
  }, true);
})();
