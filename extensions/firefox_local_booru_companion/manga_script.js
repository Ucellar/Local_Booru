'use strict';

// Local Booru on nhentai (0.9.0).  The galleries the program's manga folder has — this very one («Уже есть») or
// another version of its work («Есть: English»: another language, another upload) — are blurred (the label shows one
// and blurs it again), hidden, or only labelled, as the popup says («Манга на nhentai»).  The program tells which
// (/extension/manga): by the gallery's number, else by its title and the people who made it.  The page of a gallery
// says it over its title.  Nothing is asked of the site and nothing is sent anywhere but to the program.

const MANGA = {
  options: { enabled: true, mangaMode: 'blur', mangaVersions: true },
  known: new Map(),          // gallery number -> the program's answer (asked once a page)
  timer: null, busy: false, again: false, toldOffline: false,
};
const MANGA_OWN = '.lb-manga-label,.lb-manga-note,.lb-companion-toast';
const LANGUAGE_NAMES = { english: 'English', japanese: 'японский', chinese: 'китайский', korean: 'корейский',
  russian: 'русский', spanish: 'испанский', french: 'французский', german: 'немецкий', portuguese: 'португальский' };

function galleryNumber(href) {
  const m = String(href || '').match(/\/g\/(\d{1,8})\/?(?:[?#].*)?$/);
  return m ? Number(m[1]) : 0;
}
function shownGallery() {
  const m = location.pathname.match(/^\/g\/(\d{1,8})\/?$/);
  return m ? Number(m[1]) : 0;
}
function textOf(el) { return el ? String(el.textContent || '').replace(/\s+/g, ' ').trim() : ''; }

// A card: the element round one gallery's cover link (nhentai's «.gallery»: the covers of a list, «More Like This»),
// its title the caption.  The gallery the page shows is not a card.
function mangaCards() {
  const out = new Map();
  const here = shownGallery();
  for (const a of document.querySelectorAll('a[href*="/g/"]')) {
    const id = galleryNumber(a.getAttribute('href'));
    if (!id || id === here || out.has(id) || !a.querySelector('img') || a.closest(MANGA_OWN)) continue;
    const card = a.closest('.gallery') || a;
    const caption = card.querySelector('.caption') || a.querySelector('.caption');
    const img = a.querySelector('img');
    const title = textOf(caption) || String(a.getAttribute('title') || (img && img.getAttribute('alt')) || '').trim();
    out.set(id, { id, title, card });
  }
  return out;
}

// The gallery the page shows: its titles and the people of its tags (their links say their kind: /artist/…, /group/…).
function pageGallery() {
  const id = shownGallery();
  if (!id) return null;
  const info = document.querySelector('#info') || document.body;
  const h1 = info.querySelector('h1.title') || info.querySelector('h1');
  const h2 = info.querySelector('h2.title');
  const people = kind => [...document.querySelectorAll(`a[href*="/${kind}/"]`)].map((a) => {
    const m = String(a.getAttribute('href') || '').match(new RegExp(`/${kind}/([^/?#]+)/?$`));
    try { return m ? decodeURIComponent(m[1]).replace(/-/g, ' ') : ''; } catch (_) { return ''; }
  }).filter(Boolean);
  const title = textOf(h1) || document.title.replace(/\s*[»|–-]\s*nhentai.*$/i, '').trim();
  return { id, title, japanese: textOf(h2), artists: people('artist'), groups: people('group'), info };
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
  const title = (result.title ? `В папке манги: ${result.title}` : '') + (hint ? (result.title ? '\n' : '') + hint : '');
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
    ? 'Local Booru: эта манга уже есть в папке манги'
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
    const o = await browser.runtime.sendMessage({ type: 'LB_GET_OPTIONS' });
    if (o && o.options) MANGA.options = { ...MANGA.options, ...o.options };
    const cards = mangaCards();
    const gallery = pageGallery();
    const ask = [];
    for (const entry of cards.values()) if (!MANGA.known.has(entry.id)) ask.push({ id: entry.id, title: entry.title });
    if (gallery && !MANGA.known.has(gallery.id)) {
      ask.push({ id: gallery.id, title: gallery.title, japanese: gallery.japanese, artists: gallery.artists,
                 groups: gallery.groups });
    }
    if (ask.length && MANGA.options.enabled && (MANGA.options.mangaMode || 'blur') !== 'off') {
      const r = await browser.runtime.sendMessage({ type: 'LB_MANGA_CHECK', items: ask });
      if (r && r.offline) {
        if (!MANGA.toldOffline) { MANGA.toldOffline = true; mangaToast('Local Booru закрыта — метки манги появятся, когда она запущена'); }
      } else {
        for (const one of (r && r.items) || []) MANGA.known.set(Number(one.id), one);
        for (const one of ask) if (!MANGA.known.has(one.id)) MANGA.known.set(one.id, { id: one.id, status: '' });
      }
    }
    for (const entry of cards.values()) markCard(entry, MANGA.known.get(entry.id));
    if (gallery) markPage(gallery, MANGA.known.get(gallery.id));
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
  const result = MANGA.known.get(galleryNumber((card.matches('a') ? card : card.querySelector('a[href*="/g/"]'))?.getAttribute('href')));
  if (result) markCard({ card }, result);
}
for (const type of ['click', 'mousedown', 'mouseup', 'auxclick']) document.addEventListener(type, mangaLabelClick, true);

browser.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
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
