'use strict';
(() => {
if (window.__lbOwnedTitles) return;
window.__lbOwnedTitles = true;
const IS_NHENTAI = /(^|\.)nhentai\.net$/i.test(location.hostname);

// Downloaded source cards on supported and explicitly permitted sites. The program matches saved URLs and
// stable site/title keys; only nhentai also recognizes versions by its title and credited people. Cards are blurred,
// hidden or labelled according to the popup. No content is requested from the website; only the local program.

const MANGA = {
  options: { enabled: true, mangaMode: 'blur', mangaVersions: true },
  known: new Map(),          // source URL -> the program's answer
  timer: null, busy: false, again: false, toldOffline: false,
};
const MANGA_OWN = '.lb-manga-label,.lb-manga-note,.lb-companion-toast';
const LANGUAGE_NAMES = { english: 'English', japanese: 'японский', chinese: 'китайский', korean: 'корейский',
  russian: 'русский', spanish: 'испанский', french: 'французский', german: 'немецкий', portuguese: 'португальский' };

function galleryNumber(href) {
  if (!IS_NHENTAI) return 0;
  const m = String(href || '').match(/\/g\/(\d{1,8})\/?(?:[?#].*)?$/);
  return m ? Number(m[1]) : 0;
}
function shownGallery() { return galleryNumber(location.pathname); }
function textOf(el) { return el ? String(el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }
function titleUrl(href) {
  try {
    const u = new URL(href, location.href);
    if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password) return '';
    u.hash = '';
    if (u.pathname === '/' && !u.search) return '';
    return u.href;
  } catch (_) { return ''; }
}
function cardContainer(a) {
  const box = a.closest('.gallery,article,tr,.shortstory,.short-item,.manga-item,.content_row,.card,.tile,.item');
  if (!box) return a;
  const targets = new Set([...box.querySelectorAll('a[href]')].filter(x => x.querySelector('img,picture'))
    .map(x => titleUrl(x.href)).filter(Boolean));
  return targets.size === 1 ? box : a;
}
function mangaCards() {
  const out = [];
  const here = titleUrl(location.href);
  const seenCards = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const key = titleUrl(a.href);
    if (!key || key === here || !a.querySelector('img,picture') || a.closest(MANGA_OWN)) continue;
    const id = galleryNumber(a.href);
    if (IS_NHENTAI && !id) continue;
    const card = cardContainer(a);
    if (seenCards.has(card)) continue;
    seenCards.add(card);
    const caption = card.querySelector('.caption,h2,h3,.title');
    const img = a.querySelector('img');
    const title = textOf(caption) || String(a.title || (img && img.alt) || '').trim();
    out.push({ id, key, url: key, title, card });
  }
  return out;
}
function pageGallery() {
  const key = titleUrl(location.href);
  const id = shownGallery();
  const h1 = document.querySelector('#info h1,h1');
  if (!key || !h1 || IS_NHENTAI && !id) return null;
  const info = IS_NHENTAI ? document.querySelector('#info') : h1.parentElement;
  const people = kind => [...document.querySelectorAll(`a[href*="/${kind}/"]`)].map(a => {
    const m = String(a.getAttribute('href') || '').match(new RegExp(`/${kind}/([^/?#]+)/?$`));
    try { return m ? decodeURIComponent(m[1]).replace(/-/g, ' ') : ''; } catch (_) { return ''; }
  }).filter(Boolean);
  return { id, key, url: key, title: textOf(h1), japanese: IS_NHENTAI ? textOf(info.querySelector('h2.title')) : '',
    artists: IS_NHENTAI ? people('artist') : [], groups: IS_NHENTAI ? people('group') : [], info };
}

function languagesText(result) {
  return (result.languages || []).map(x => LANGUAGE_NAMES[x] || x).join(', ');
}
function labelText(result) {
  if (result.status === 'same') return 'Уже есть';
  const langs = languagesText(result);
  return langs ? `Есть: ${langs}` : 'Есть другая версия';
}
function counts(result) {
  return result && (result.status === 'same' || (result.status === 'version' && MANGA.options.mangaVersions !== false));
}

function markCard(entry, result) {
  const { card } = entry;
  if (entry.key) card.dataset.lbOwnedKey = entry.key;
  const mode = MANGA.options.mangaMode || 'blur';
  let label = card.querySelector(':scope > .lb-manga-label');
  if (!counts(result) || mode === 'off' || !MANGA.options.enabled) {
    card.classList.remove('lb-manga-marked', 'lb-manga-blur', 'lb-manga-hidden', 'lb-manga-shown');
    if (label) label.remove();
    return;
  }
  card.classList.add('lb-manga-marked');
  card.classList.toggle('lb-manga-blur', mode === 'blur');
  card.classList.toggle('lb-manga-hidden', mode === 'hide');
  if (!label) {
    label = document.createElement('button');
    label.type = 'button';
    label.className = 'lb-manga-label';
    card.appendChild(label);
  }
  const text = labelText(result);
  if (label.textContent !== text) label.textContent = text;
  label.dataset.status = result.status;
  const shown = card.classList.contains('lb-manga-shown');
  const hint = mode === 'blur' ? (shown ? 'Щёлкните, чтобы размыть снова' : 'Щёлкните, чтобы показать') : '';
  const title = (result.title ? `В библиотеке: ${result.title}` : '') + (hint ? (result.title ? '\n' : '') + hint : '');
  if (label.title !== title) label.title = title;
}

function markPage(gallery, result) {
  let note = document.querySelector('.lb-manga-note');
  if (!counts(result) || !MANGA.options.enabled || (MANGA.options.mangaMode || 'blur') === 'off') {
    if (note) note.remove();
    return;
  }
  if (!note) {
    note = document.createElement('div');
    note.className = 'lb-manga-note';
    note.setAttribute('role', 'note');
    (gallery.info || document.body).prepend(note);
  }
  const langs = languagesText(result);
  const text = result.status === 'same'
    ? 'Local Booru: этот материал уже есть в библиотеке'
    : `Local Booru: в папке манги есть другая версия этой манги${langs ? ` (${langs})` : ''}: ${result.title || ''}`;
  if (note.textContent !== text) note.textContent = text;
  note.dataset.status = result.status;
}

function mangaToast(text) {
  document.querySelector('.lb-companion-toast')?.remove();
  const el = document.createElement('div');
  el.className = 'lb-companion-toast';
  el.textContent = text;
  document.documentElement.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

async function checkManga() {
  if (MANGA.busy) { MANGA.again = true; return; }
  MANGA.busy = true;
  try {
    const o = await chrome.runtime.sendMessage({ type: 'LB_GET_OPTIONS' });
    if (o && o.options) MANGA.options = { ...MANGA.options, ...o.options };
    const cards = mangaCards();
    const gallery = pageGallery();
    const pending = new Map();
    for (const entry of cards.values()) if (!MANGA.known.has(entry.key)) pending.set(entry.key,
      { id: entry.id, key: entry.key, url: entry.url, title: entry.title });
    if (gallery && !MANGA.known.has(gallery.key)) pending.set(gallery.key,
      { id: gallery.id, key: gallery.key, url: gallery.url, title: gallery.title, japanese: gallery.japanese,
        artists: gallery.artists, groups: gallery.groups });
    const ask = [...pending.values()].slice(0, 300);
    if (ask.length && MANGA.options.enabled && (MANGA.options.mangaMode || 'blur') !== 'off') {
      const r = await chrome.runtime.sendMessage({ type: 'LB_MANGA_CHECK', items: ask });
      if (r && r.offline) {
        if (!MANGA.toldOffline) { MANGA.toldOffline = true; mangaToast('Local Booru закрыта — метки скачанного появятся, когда она запущена'); }
      } else {
        for (const one of (r && r.items) || []) MANGA.known.set(one.key, one);
        for (const one of ask) if (!MANGA.known.has(one.key)) MANGA.known.set(one.key, { key: one.key, status: '' });
      }
    }
    for (const entry of cards.values()) markCard(entry, MANGA.known.get(entry.key));
    if (gallery) markPage(gallery, MANGA.known.get(gallery.key));
    if (pending.size > ask.length && ask.every(x => MANGA.known.has(x.key))) MANGA.again = true;
  } catch (_) {
    /* the program does not answer: no marks; the popup says why */
  } finally {
    MANGA.busy = false;
    if (MANGA.again) { MANGA.again = false; scheduleManga(); }
  }
}
function scheduleManga() { clearTimeout(MANGA.timer); MANGA.timer = setTimeout(checkManga, 300); }

// the label of a blurred card shows it (and blurs it again); never the gallery's link under it
function mangaLabelClick(ev) {
  const label = ev.target && ev.target.closest ? ev.target.closest('.lb-manga-label') : null;
  if (!label) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (ev.type !== 'click') return;
  const card = label.parentElement;
  if (!card.classList.contains('lb-manga-blur')) return;
  card.classList.toggle('lb-manga-shown');
  const result = MANGA.known.get(card.dataset.lbOwnedKey);
  if (result) markCard({ card }, result);
}
for (const type of ['click', 'mousedown', 'mouseup', 'auxclick']) document.addEventListener(type, mangaLabelClick, true);

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.offlineIndex || changes.mangaSitesRev) { MANGA.known.clear(); scheduleManga(); }
  if (changes.mangaMode || changes.mangaVersions || changes.enabled) scheduleManga();
});
// new covers (the next page loaded in place, «More Like This») and the site's own moves between pages
let lastPath = location.pathname;
new MutationObserver((ms) => {
  if (location.pathname !== lastPath) { lastPath = location.pathname; document.querySelector('.lb-manga-note')?.remove(); }
  const own = n => { const el = n && (n.nodeType === 1 ? n : n.parentElement); return !!(el && el.closest && el.closest(MANGA_OWN)); };
  if (ms.some(m => !own(m.target) && [...(m.addedNodes || [])].some(n => !own(n)))) scheduleManga();
}).observe(document.documentElement, { childList: true, subtree: true });
scheduleManga();

// New downloads are reflected without reloading the site's page. No request while the tab is hidden.
setInterval(() => { if (!document.hidden) { MANGA.known.clear(); scheduleManga(); } }, 30000);
})();
