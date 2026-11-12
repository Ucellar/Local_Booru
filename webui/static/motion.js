// Local Booru (v5.44.55–56): softness — the content of a page comes in softly instead of jumping (the header stays as
// it is), a page turned forward comes from the right and back from the left, pictures appear as they come.  Written
// into the <head> of every page, after its stylesheets (webui/motion_script.py): it runs before the page is first
// drawn, so the way the page came in is known to the styles from the first picture, and before any picture has come.
// The styles are in booru.css (all of them under :root[data-motion="on"], which this script sets): «Плавные
// переходы» in the menu «Оформление» turns them off (webui_prefs.json: motion), and so do the system's «less motion»
// and a browser driven by a program (the checks: their pages must be still — sessionStorage lbMotionTest lets one in).
// (v5.44.56) No view transitions between the pages any more: one look melted into another, the change was too big,
// and the snapshots of pages loaded the graphics of the program's window.
// (v5.44.58) The way out: the pictures still coming are let go when the reader leaves, so the next page does not
// wait for them (below; not part of the softness — it works with «Плавные переходы» off too).
// (v5.44.158; the owner: «анимаций как будто вообще нет», the mockup «Анимации» approved 11.11.2026) Three positions,
// not two: «Нет», «Спокойные», «Заметные» (data-motion-pref: none, calm, vivid — «Заметные» until chosen otherwise).
// data-motion stays «on» / «off» (whether anything moves), data-motion-level says how much: the styles take their
// lengths, distances and easing from it (booru.css: --m-…).  Tiles lift under the pointer, buttons give under a
// press, the tiles of a list come one after another, menus and sheets open, the bars of «Загрузки» run.
(() => {
  "use strict";
  const root = document.documentElement;
  const pref = root.dataset.motionPref === "none" ? "none" : root.dataset.motionPref === "calm" ? "calm" : "vivid";
  let on = pref !== "none";
  try {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) on = false;
    if (navigator.webdriver && !window.sessionStorage.getItem("lbMotionTest")) on = false;
  } catch (_) {
    if (navigator.webdriver) on = false;
  }
  root.dataset.motion = on ? "on" : "off";
  root.dataset.motionLevel = on ? pref : "off";

  // ── which way the reader went ─────────────────────────────────────────────────────────────────────────────────
  // The page left says whether it was to its «next» or «prev» link (the reader's pages, a card's neighbours, the pages
  // of a list, the episodes); the page that comes takes it — if it is the very page that was asked for.
  function hrefs(rel) {
    return Array.from(document.querySelectorAll(`a[rel~="${rel}"][href]`), (a) => a.href);
  }

  window.addEventListener("pageswap", (event) => {
    const to = event.activation && event.activation.entry ? event.activation.entry.url : "";
    let way = "";
    if (to && hrefs("next").includes(to)) way = "next";
    else if (to && hrefs("prev").includes(to)) way = "prev";
    try {
      if (way && root.dataset.motion === "on") window.sessionStorage.setItem("lbTurn", JSON.stringify({ to, way }));
      else window.sessionStorage.removeItem("lbTurn");
    } catch (_) { /* no session storage: the page just comes in */ }
  });

  if (on) {
    let said = null;
    try {
      said = JSON.parse(window.sessionStorage.getItem("lbTurn") || "null");
      window.sessionStorage.removeItem("lbTurn");
    } catch (_) {
      said = null;
    }
    const way = said && said.to === window.location.href && (said.way === "next" || said.way === "prev") ? said.way : "";
    root.dataset.turned = way || "fade";             // how this page comes in (booru.css; the checks read it)
    // a page come back to (the browser's «back») stands at the place left, far down its list: the tiles there would
    // all wait for the turn of the last — they come together with the page
    let came = null;
    try { came = window.performance.getEntriesByType("navigation")[0]; } catch (_) { came = null; }
    if (came && came.type === "back_forward") root.dataset.came = "back";
    // while the content comes in it is drawn a little off its place: what would stick out of the page's frame for
    // that moment is cut (booru.css: data-coming), or a scroll bar would flash at the window's edge
    root.dataset.coming = "1";
    const there = (event) => {
      if (!event || (event.target instanceof Element && event.target.matches("main.content"))) delete root.dataset.coming;
    };
    document.addEventListener("animationend", there, true);
    document.addEventListener("animationcancel", there, true);
    document.addEventListener("DOMContentLoaded", () => window.setTimeout(there, 1200));   // nothing moved after all
  }

  // ── pictures as they come: hidden until loaded, then faded in (booru.css; a broken one is shown at once) ──
  // those already there when this script runs (nothing is drawn yet) are simply shown
  document.querySelectorAll("img").forEach((img) => {
    if (img.complete) img.classList.add("lb-in", "lb-now");
  });
  function arrived(event) {
    const img = event.target;
    if (img && img.tagName === "IMG") img.classList.add("lb-in");
  }
  document.addEventListener("load", arrived, true);
  document.addEventListener("error", arrived, true);
  // a picture whose news was missed (the page restored as it was left) is not kept hidden
  window.addEventListener("load", () => {
    window.setTimeout(() => {
      document.querySelectorAll("img:not(.lb-in)").forEach((img) => {
        if (img.complete) img.classList.add("lb-in");
      });
    }, 1200);
  });

  // ── the way out: the pictures still coming do not hold the next page (v5.44.58) ─────────────────────────────
  // A browser talks to the program over six connections at most.  A page whose pictures are still being made (new
  // thumbnails, frames of videos, covers) or still coming from a site (the grabber's previews) holds all six, and the
  // page asked for next waited until one of them was done: a click «hung» for a second or for many (the owner,
  // 4.11.2026: «как будто прога зависает» — the grabber and the gallery, at times the parser and the manga).  So when
  // the reader leaves for another page of the program, the pictures still coming are let go: their connections
  // close, the server does not make the ones it has not begun (webui/server.py), the next page is asked at once.  If
  // the page stays after all (a file given to save, «leave the page?» answered «stay»), they come back.
  const LIVE = /\/video\/live\//;                    // the window's own player: its stream is never let go
  let gone = [];
  function bringBack() {
    const list = gone;
    gone = [];
    list.forEach(([img, src, srcset]) => {
      if (img.hasAttribute("src")) return;           // the page has put another picture there meanwhile
      if (srcset !== null) img.setAttribute("srcset", srcset);
      img.setAttribute("src", src);
    });
  }
  function letGo() {
    document.querySelectorAll("img[src]").forEach((img) => {
      const src = img.getAttribute("src");
      if (img.complete || !src || LIVE.test(src)) return;
      gone.push([img, src, img.getAttribute("srcset")]);
      img.removeAttribute("srcset");
      img.removeAttribute("src");
    });
    if (gone.length) window.setTimeout(bringBack, 4000);
  }
  function ours(href) {
    try {
      return new URL(href, window.location.href).origin === window.location.origin;
    } catch (_) {
      return false;
    }
  }
  if (window.navigation && typeof window.navigation.addEventListener === "function") {
    window.navigation.addEventListener("navigate", (event) => {
      if (event.hashChange || event.downloadRequest !== null) return;
      if (event.destination.sameDocument || !ours(event.destination.url)) return;
      letGo();
    });
  } else {                                           // a browser without the Navigation API: the links at least
    window.addEventListener("click", (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey ||
          event.altKey) return;
      const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self") || !ours(link.href)) return;
      const to = new URL(link.href, window.location.href);
      if (to.pathname === window.location.pathname && to.search === window.location.search) return;
      letGo();
    });
  }
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) bringBack();                // the page kept as it was left and shown again
  });
})();
