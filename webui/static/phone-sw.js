/* (v5.44.69) The phone's service worker (served as /m/sw.js: its scope is the phone's pages, webui/pages/phone.py).
   It does one thing: a page of the phone that cannot be had — the computer is off or asleep, the program is not
   running, Tailscale cannot reach it (its 502 / 504), (v5.44.71) the owner's server cannot (its 502 / 503 / 504) — is
   answered with the page «Компьютер не отвечает» kept here.  Pictures, videos and the program's answers are never
   touched: they go to the computer as they are. */
"use strict";

const KEEP = "lb-phone-v2";
const DOWN = [502, 503, 504];
const OFFLINE = "/m/offline";
const ASSETS = ["/static/phone.css", "/static/phone.js"];      // the offline page's own look and words

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(KEEP)
    .then((box) => box.addAll([OFFLINE, ...ASSETS]))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((names) => Promise.all(names.filter((name) => name !== KEEP).map((name) => caches.delete(name))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const address = new URL(event.request.url);
  if (event.request.mode !== "navigate") {
    if (address.origin === self.location.origin && ASSETS.includes(address.pathname)) {
      event.respondWith(fetch(event.request).catch(() => caches.match(address.pathname)
        .then((kept) => kept || Response.error())));
    }
    return;
  }
  event.respondWith(fetch(event.request)
    .then((answer) => (DOWN.includes(answer.status)
      ? caches.match(OFFLINE).then((kept) => kept || answer) : answer))
    .catch(() => caches.match(OFFLINE).then((kept) => kept || Response.error())));
});
