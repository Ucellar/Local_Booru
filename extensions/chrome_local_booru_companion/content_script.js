'use strict';

// Every card on a booru page is described by itself: its own post link, post id, picture and
// tags.  The page (location.href, og:image, the page's pool and author) belongs only to the post
// the page shows — never to the grid cards or «related» links around it.

const LB = {
  cardAttr: 'data-local-booru-card-id', lastContextTarget: null,
  options: { enabled: true, hideMode: 'hide', showBadge: true, dock: 'tab', profiles: [] },
  profile: null, observer: null, pendingTimer: null, seq: 1,
  selected: new Set(), lastAnchor: -1, results: new Map(), autoQueued: new Set(),
  items: new Map(), offlineNoticed: false,
  sub: null, blocked: 0,
};
const OWN_NODES = '.lb-known-badge,.lb-companion-toast,.lb-selection-toolbar,.lb-blocked-label';
const CARD_SELECTOR = 'article,.post-preview,.thumbnail-preview,.thumb,.image-list span,.content span,.post,.preview,li,[data-id],[data-post-id]';
const MAIN_MEDIA_SELECTOR = '#image,#main_image,#image-container img,#image-container video,.image-container img,.image-container video,#gelcomVideoPlayer,video';

function canonicalHost() {
  const h = location.hostname.toLowerCase().replace(/^www\./, '');
  if (h.endsWith('rule34.xxx')) return 'rule34.xxx';
  if (h.endsWith('gelbooru.com')) return 'gelbooru.com';
  if (h === 'danbooru.donmai.us') return h;
  if (h === 'e621.net' || h === 'e926.net') return h;
  if (h === 'booru.allthefallen.moe') return h;
  if (h === 'pixiv.net') return 'pixiv.net';
  return h;
}
function absUrl(v) { try { return v ? new URL(v, location.href).href.replace(/\/$/, '') : ''; } catch (_) { return ''; } }
function md5FromText(text) { const m = String(text || '').match(/(?:^|[^0-9a-f])([0-9a-f]{32})(?:[^0-9a-f]|$)/i); return m ? m[1].toLowerCase() : ''; }
function postIdFromUrl(url) {
  try {
    const u = new URL(url, location.href);
    const id = u.searchParams.get('id');
    if (id && /^\d+$/.test(id)) return id;
    const m = u.pathname.match(/\/(?:posts|post\/show|artworks)\/(\d+)/);
    return m ? m[1] : '';
  } catch (_) { return ''; }
}
function looksLikeMediaUrl(url) { return /\.(?:jpe?g|png|gif|webp|avif|bmp|mp4|webm)(?:[?#].*)?$/i.test(String(url || '')); }
function isPageElement(el) { return !el || el === document.body || el === document.documentElement; }
function closestCard(el) { const card = el && el.closest ? el.closest(CARD_SELECTOR) : null; return isPageElement(card) ? null : card; }

function wildcardRegex(pattern) { return new RegExp('^' + String(pattern).replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$', 'i'); }
function profileMatches(profile) {
  if (!profile || !Array.isArray(profile.origins)) return false;
  return profile.origins.some(pattern => { try { return wildcardRegex(pattern).test(location.href); } catch (_) { return false; } });
}
function findProfile(profiles) {
  const rows = profiles || [];
  return rows.find(p => p.site_id === canonicalHost() && profileMatches(p)) || rows.find(p => p.site_id !== 'generic' && profileMatches(p))
    || rows.find(p => p.site_id === 'generic') || null;
}
function querySafe(root, selector) { try { return Array.from((root || document).querySelectorAll(selector)); } catch (_) { return []; } }
function valueFrom(el, attr) { if (!el) return ''; if (attr === 'textContent') return String(el.textContent || '').trim(); return String(el.getAttribute && el.getAttribute(attr) || '').trim(); }
// MD5 by the profile's selector rules (e.g. Danbooru's data-md5), on the card itself or inside it.
function md5FromRules(card, rules) {
  for (const rule of rules || []) {
    if (!rule.selector) continue;
    const els = [];
    try { if (card.matches(rule.selector)) els.push(card); } catch (_) {}
    els.push(...querySafe(card, rule.selector));
    for (const el of els) { const v = md5FromText(valueFrom(el, rule.attribute || 'textContent')); if (v) return v; }
  }
  return '';
}
// A profile rule with source "url" reads the URL of the card's own post, never the page address:
// on a post page every «related» thumbnail would otherwise get the id of the page's post.
function regexExtract(text, rules, url) {
  for (const rule of rules || []) {
    if (rule.source !== 'html' && rule.source !== 'url') continue;
    try {
      const src = rule.source === 'url' ? String(url || '') : text;
      if (!src) continue;
      const normalized = String(rule.pattern || '').replace(/\(\?<value>/, '(');
      const m = src.match(new RegExp(normalized, 'i'));
      if (m) return String(m.groups && m.groups.value || m[1] || '');
    } catch (_) {}
  }
  return '';
}

function normalizeTag(value) {
  let t = String(value || '').trim();
  try { t = decodeURIComponent(t); } catch (_) {}
  t = t.replace(/^[-+]+/, '').replace(/\s+/g, '_');
  if (!t || t.length > 180 || /^(?:rating|score|source|user|order|status):/i.test(t)) return '';
  return t;
}
function genericTagFromLink(a) {
  try {
    const u = new URL(a.href, location.href);
    let raw = u.searchParams.get('tags') || '';
    if (!raw) { const m = u.pathname.match(/\/tags\/([^/?#]+)/); if (m) raw = m[1]; }
    return normalizeTag(raw || a.getAttribute('data-tag-name') || a.textContent);
  } catch (_) { return ''; }
}
// (0.9.1) Not a tag of the post: «all» — every post of a Gelbooru-like site (its menu links tags=all); a grid's link to a
// post (its tags= is the search the grid was opened with); the links of the site's menu and pages.
const JUNK_TAGS = new Set(['all']);
const NOT_TAG_LINKS = 'nav,header,footer,#navbar,#subnavbar,.navbar,.pagination,#paginator,.paginator';
// The category of a tag by the list item it stands in: Gelbooru-likes and Danbooru mark it (li.tag-type-artist,
// li.tag-type-1), e621 names it (data-category).
const TAG_TYPES = { artist: 'artist', character: 'character', copyright: 'copyright', metadata: 'meta', meta: 'meta',
  general: 'general', species: 'species', lore: 'lore', contributor: 'contributor', invalid: 'invalid',
  '0': 'general', '1': 'artist', '3': 'copyright', '4': 'character', '5': 'meta' };
function tagCategory(el) {
  const holder = el && el.closest ? el.closest('[data-category],[class*="tag-type-"]') : null;
  if (!holder) return 'general';
  const named = String(holder.getAttribute('data-category') || '').toLowerCase();
  const m = String(holder.className || '').match(/tag-type-([a-z0-9]+)/i);
  return TAG_TYPES[named] || (m && TAG_TYPES[m[1].toLowerCase()]) || 'general';
}
function extractGroups(root) {
  const groups = { general: [] };
  const seen = new Set();
  const add = (value, el) => {
    const tag = normalizeTag(value);
    if (!tag || JUNK_TAGS.has(tag.toLowerCase()) || seen.has(tag)) return;
    seen.add(tag);
    const category = tagCategory(el);
    (groups[category] = groups[category] || []).push(tag);
  };
  const rules = (LB.profile && LB.profile.selectors && LB.profile.selectors.tags) || [];
  let found = 0;
  for (const rule of rules) {
    if (!rule.selector) continue;
    for (const el of querySafe(root, rule.selector)) { const v = valueFrom(el, rule.attribute || 'textContent'); if (v) { found += 1; add(v, el); } }
  }
  if (!found) {
    for (const a of querySafe(root, "a[href*='tags='],a[href*='/tags/'],[data-tag-name]")) {
      if (a.closest(NOT_TAG_LINKS) || (a.matches('a[href]') && isPostLink(a))) continue;
      add(a.getAttribute('data-tag-name') || genericTagFromLink(a), a);
    }
  }
  return groups;
}
function flattenGroups(groups) { return Object.values(groups || {}).flat().filter((x, i, a) => x && a.indexOf(x) === i); }

// The card itself and what is inside it (Danbooru keeps data-file-url / data-md5 on the card).
function scanTargets(root, selector) {
  const out = [];
  try { if (root && root.matches && root.matches(selector)) out.push(root); } catch (_) {}
  return out.concat(querySafe(root, selector));
}
// Media of one card — for the post a page shows, of its main picture block plus og:image, which
// describes the page (never the «related» thumbnails around it).  Links count only when they point
// at a file.
function collectMediaUrls(root, target, pageMeta) {
  const scored = new Map();
  const add = (v, score = 1) => {
    const u = absUrl(v);
    if (!u) return;
    scored.set(u, Math.max(scored.get(u) || 0, score + (looksLikeMediaUrl(u) ? 20 : 0)));
  };
  const rules = (LB.profile && LB.profile.selectors && LB.profile.selectors.media) || [];
  for (const rule of rules) for (const el of scanTargets(root, rule.selector)) add(valueFrom(el, rule.attribute || 'src'), 100);
  if (pageMeta) { const og = document.querySelector("meta[property='og:image'],meta[name='twitter:image']"); if (og) add(og.content, 80); }
  for (const el of scanTargets(root, 'img,video,source,a[href],[data-file-url],[data-original],[data-original-url],[data-large-file-url],[data-download-url],[data-sample-url]')) {
    for (const attr of ['data-file-url', 'data-original-url', 'data-original', 'data-download-url', 'data-large-file-url', 'data-source-file-url', 'data-sample-url', 'src']) add(el.getAttribute && el.getAttribute(attr), 40);
    const href = el.getAttribute && el.getAttribute('href');
    if (href && looksLikeMediaUrl(absUrl(href))) add(href, 40);
    add(el.currentSrc, 50);
    if (el.srcset) for (const part of el.srcset.split(',')) add(part.trim().split(/\s+/)[0], 45);
  }
  if (target) add(target.currentSrc || target.src || target.href, 120);
  return [...scored.entries()].sort((a, b) => b[1] - a[1]).map(x => x[0]).filter((x, i, a) => a.indexOf(x) === i);
}
function mediaDimensions(root) {
  let best = { width: 0, height: 0 };
  for (const el of querySafe(root, 'img,video')) {
    const w = Number(el.naturalWidth || el.videoWidth || el.width || 0), h = Number(el.naturalHeight || el.videoHeight || el.height || 0);
    if (w * h > best.width * best.height) best = { width: w, height: h };
  }
  return best;
}
function extractNotes(root) {
  const cfg = LB.profile && LB.profile.notes || {};
  const selector = cfg.selector || '.note-box';
  const notes = [];
  querySafe(root, selector).forEach((el, index) => {
    const r = el.getBoundingClientRect();
    const parent = (el.offsetParent || root || document.documentElement).getBoundingClientRect?.() || { left: 0, top: 0, width: 1, height: 1 };
    let body = '';
    if (cfg.body_selector) { const b = el.querySelector(cfg.body_selector); body = String(b && b.textContent || '').trim(); }
    if (!body) body = String(el.getAttribute('title') || el.dataset?.body || el.textContent || '').trim();
    if (body) notes.push({ id: String(el.dataset?.id || index), x: r.left - parent.left, y: r.top - parent.top, width: r.width, height: r.height, body });
  });
  return notes;
}
function originalSource() {
  const mirrors = new Set(['rule34.xxx', 'gelbooru.com', 'danbooru.donmai.us', 'e621.net', 'e926.net', 'booru.allthefallen.moe']);
  if (!mirrors.has(canonicalHost())) return absUrl(location.href);
  const a = document.querySelector("a[href*='pixiv.net'],a[href*='twitter.com'],a[href*='x.com'],a[href*='bsky.app'],a[href*='furaffinity.net'],a[href*='patreon.com'],a[href*='fanbox.cc']");
  return absUrl(a && a.href) || '';
}
function authorData() {
  const meta = document.querySelector("meta[name='author'],meta[property='article:author']");
  const a = document.querySelector("a[href*='/users/'],a[href*='/user/'],a[rel='author']");
  return { author_name: String(meta && meta.content || a && a.textContent || '').trim(), author_id: postIdFromUrl(a && a.href || '') };
}

function isPostLink(a) {
  const url = absUrl(a && a.href);
  if (!url) return false;
  if (/page=post[^#]*[?&]s=view/i.test(url) || /\/(?:posts|post\/show|artworks)\/\d+/i.test(url)) return true;
  return ((LB.profile && LB.profile.post_url_patterns) || []).some(p => { try { return wildcardRegex(p).test(url); } catch (_) { return false; } });
}
function candidateLinks(root) { return querySafe(root || document, 'a[href]').filter(isPostLink); }
function isPostPage() {
  const here = absUrl(location.href);
  if (/\/(?:posts|post\/show|artworks)\/\d+/i.test(location.pathname) || /page=post[^#]*[?&]s=view/i.test(location.href)) return true;
  return ((LB.profile && LB.profile.post_url_patterns) || []).some(p => { try { return wildcardRegex(p).test(here); } catch (_) { return false; } });
}
function samePost(url, other) {
  const a = postIdFromUrl(url), b = postIdFromUrl(other);
  if (a && b) return a === b;
  return absUrl(url) === absUrl(other);
}

function familyFromPage(postId) {
  // The pages of a pixiv work come from pixiv itself (LB_PIXIV_IMPORT_WORK), not from the URLs on the page.
  if (canonicalHost() === 'pixiv.net' && postId) return { source: 'pixiv.net', type: 'pixiv_work', external_id: postId, index: 0, total: 0, items: [] };
  const pool = querySafe(document, "a[href*='page=pool'],a[href*='/pools/']")[0];
  if (pool) { const id = postIdFromUrl(pool.href) || pool.href; return { source: canonicalHost(), type: 'booru_pool', external_id: id, index: 0, total: 0, items: [] }; }
  return {};      // several URLs of one post are its original, sample and thumbnail — not pages of a family
}

// The element that stands for the post shown by a post page: the main picture's own block,
// never the page body (hiding the body hid the whole page).
function mainMedia(target) {
  if (target && /^(IMG|VIDEO)$/.test(target.tagName || '') && !closestCard(target)) return target;
  const picks = querySafe(document, MAIN_MEDIA_SELECTOR).filter(el => !closestCard(el) || el.closest('#image-container,.image-container'));
  const minArea = picks.length ? -1 : 250 * 250;        // a guess from the page: only a big picture, not a logo
  let best = null, area = minArea;
  for (const el of picks.length ? picks : querySafe(document, 'img,video')) {
    if (!picks.length && closestCard(el)) continue;
    const w = Number(el.naturalWidth || el.videoWidth || el.width || 0), h = Number(el.naturalHeight || el.videoHeight || el.height || 0);
    if (w * h > area) { best = el; area = w * h; }
  }
  return best;
}
function currentPostCard(target) {
  const media = mainMedia(target);
  if (!media) return null;
  let card = media.closest('#image-container,.image-container,#gelcomVideoContainer,figure') || media.parentElement;
  while (card && /^(A|PICTURE|SOURCE)$/.test(card.tagName) && !isPageElement(card.parentElement)) card = card.parentElement;
  return isPageElement(card) ? null : card;
}

function buildItem(card, postUrl, current, target) {
  if (!card) return null;
  if (!card.getAttribute(LB.cardAttr)) card.setAttribute(LB.cardAttr, 'lb-' + (LB.seq++));
  card.classList.add('lb-companion-card');
  const mediaUrls = collectMediaUrls(card, target, current);
  const groups = extractGroups(current ? document : card);
  const html = String(card.outerHTML || '').slice(0, 20000);
  const rules = (LB.profile && LB.profile.selectors) || {};
  const md5 = md5FromRules(card, rules.md5) || regexExtract(html, rules.md5, postUrl) || md5FromText(html + ' ' + mediaUrls.join(' '));
  const explicit = current ? '' : (card.getAttribute('data-id') || card.getAttribute('data-post-id') || '');
  const pid = explicit || regexExtract('', rules.post_id, postUrl) || postIdFromUrl(postUrl);
  const dims = mediaDimensions(current ? (card.querySelector('img,video') ? card : document) : card);
  const author = current ? authorData() : { author_name: '', author_id: '' };
  const item = {
    key: card.getAttribute(LB.cardAttr), identity_v: 2, site: canonicalHost(), post_id: pid, post_url: postUrl, page_url: absUrl(location.href),
    file_url: mediaUrls[0] || '', media_urls: mediaUrls, md5, groups, tags: flattenGroups(groups), page_title: document.title || '',
    is_post_page: !!current, width: dims.width, height: dims.height, original_source: current ? originalSource() : '',
    notes: extractNotes(current ? document : card), ...author, family: current ? familyFromPage(pid) : {},
  };
  LB.items.set(item.key, item);
  return item;
}
function itemFromLink(a, target = null) { return buildItem(closestCard(a) || a, absUrl(a.href), false, target); }
function currentPostItem(target = null) { return isPostPage() ? buildItem(currentPostCard(target), absUrl(location.href), true, target) : null; }
// What the user right-clicked: the post of the card/link under the cursor; the page's own post only
// when the click was on it; otherwise just the clicked file (no post: nothing is guessed from the page).
function itemFromTarget(target) {
  if (!target || !target.closest) return null;
  const link = target.closest('a[href]');
  const card = closestCard(target);
  const a = (link && isPostLink(link) ? link : null) || (card ? candidateLinks(card)[0] : null);
  if (a) return isPostPage() && samePost(a.href, location.href) ? currentPostItem(target) : itemFromLink(a, target);
  if (isPostPage()) return currentPostItem(target);
  const media = absUrl(target.currentSrc || target.src || (link && looksLikeMediaUrl(absUrl(link.href)) ? link.href : '') || '');
  if (!media) return null;
  const holder = card || (isPageElement(target.parentElement) ? target : target.parentElement);
  const item = buildItem(holder, '', false, target);
  item.media_urls = [media];
  item.file_url = media;
  return item;
}
function itemKey(item) {
  return item.md5 || (item.post_id ? item.site + ':' + item.post_id : '') || item.post_url || item.file_url || item.key;
}
function collectItems() {
  const seen = new Set(), out = [];
  const page = currentPostItem();
  if (page) { seen.add(itemKey(page)); out.push(page); }
  for (const a of candidateLinks(document)) {
    if (page && samePost(a.href, page.post_url)) continue;          // a link to the post the page shows
    const holder = closestCard(a) || a;
    if (!holder.querySelector('img,video,picture')) continue;        // «next post», a post number in a comment: not a card
    const item = itemFromLink(a);
    if (!item) continue;
    const k = itemKey(item);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(item);
    if (out.length >= 500) break;
  }
  if (!out.length) { const item = itemFromTarget(LB.lastContextTarget); if (item && item.media_urls.length) out.push(item); }
  return out;
}
function cardByKey(key) { try { return document.querySelector('[' + LB.cardAttr + '="' + CSS.escape(key) + '"]'); } catch (_) { return null; } }
// Rewriting the same text would be seen by the page observer as new content and start another check.
function badge(card, text, status, imageId) {
  if (!LB.options.showBadge || !card) return;
  let b = card.querySelector(':scope > .lb-known-badge');
  const id = String(Number(imageId || 0) || '');
  if (b && b.textContent === text && b.dataset.status === (status || '') && (b.dataset.image || '') === id) return;
  if (!b) { b = document.createElement('span'); b.className = 'lb-known-badge'; card.appendChild(b); }
  b.textContent = text;
  b.dataset.status = status || '';
  if (id) { b.dataset.image = id; b.title = 'Открыть в Local Booru'; } else { delete b.dataset.image; b.removeAttribute('title'); }
}
// «Уже есть» on a card: a click opens that card in the program's window (and not the site's post under it)
function openKnown(ev) {
  if (ev.altKey) return;                        // Alt+click chooses the card
  const b = ev.target && ev.target.closest ? ev.target.closest('.lb-known-badge[data-image]') : null;
  if (!b) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (ev.type !== 'click') return;
  chrome.runtime.sendMessage({ type: 'LB_OPEN_CARD', imageId: Number(b.dataset.image) })
    .then(r => toast(r && r.ok ? 'Открыто в Local Booru' : 'Не открылось: ' + String(r && r.error || 'программа не ответила')))
    .catch(e => toast('Не открылось: ' + String(e && e.message || e)));
}
function toast(text) {
  document.querySelector('.lb-companion-toast')?.remove();
  const el = document.createElement('div');
  el.className = 'lb-companion-toast';
  el.textContent = text;
  document.documentElement.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

function offlineQueuedText(r) { return `Local Booru закрыта — в очереди ${Number(r && r.offline_queue || 0)}; уйдёт в программу, когда она запустится`; }
function sentText(r, okText) {
  if (r && r.ok === false) return 'Не отправлено: ' + String(r.error || 'ошибка');
  if (r && r.queued_offline) return offlineQueuedText(r);
  return okText;
}
async function autoQueueUpgrade(result, item) {
  const key = itemKey(item);
  if (LB.autoQueued.has(key)) return;
  LB.autoQueued.add(key);
  try {
    const r = await chrome.runtime.sendMessage({ type: 'LB_IMPORT_ITEM', item });
    toast(sentText(r, 'Local Booru: более качественная версия поставлена на автоматическую замену'));
  } catch (e) { toast('Ошибка автоапгрейда: ' + String(e && e.message || e)); }
}
function applyResult(result) {
  if (!result || !result.key) return;
  const card = cardByKey(result.key);
  if (!card) return;
  const item = LB.items.get(result.key);
  const current = !!(item && item.is_post_page);
  LB.results.set(result.key, result);
  card.dataset.localBooruStatus = result.status || 'new';
  if (result.status === 'upgrade_available') {
    badge(card, `АПГРЕЙД ${result.local_width}×${result.local_height} → ${result.remote_width}×${result.remote_height}`, 'upgrade');
    if (result.auto_upgrade && item) autoQueueUpgrade(result, item);
    return;
  }
  if (result.status === 'post_different_file') { badge(card, 'Пост есть, файл другой', 'variant'); return; }
  if (result.status === 'downloaded') {
    const family = Number(result.family_total || 0) > 1 ? ` · семейство ${Number(result.family_present || 0)}/${Number(result.family_total || 0)}` : '';
    badge(card, 'Уже есть' + family, Number(result.family_total || 0) > Number(result.family_present || 0) ? 'family-partial' : 'known',
          result.image_id);
  }
  // The post the page shows is only marked: hiding it would leave an empty page.
  if (result.action === 'hide' && !current) {
    if ((LB.options.hideMode || 'hide') === 'dim') card.classList.add('lb-known-dim');
    else card.classList.add('lb-known-hidden');
  }
}

function selectedItems() { const map = new Map(collectItems().map(x => [x.key, x])); return [...LB.selected].map(k => map.get(k)).filter(Boolean); }
async function sendSelected(mode = 'selected') {
  const all = collectItems();
  let items = mode === 'all' ? all : selectedItems();
  if (mode === 'new') items = all.filter(x => { const r = LB.results.get(x.key); return !r || r.status === 'new' || r.status === 'post_different_file'; });
  if (mode === 'upgrades') items = all.filter(x => LB.results.get(x.key)?.status === 'upgrade_available');
  if (!items.length) { toast('Нечего отправлять'); return; }
  const r = await chrome.runtime.sendMessage({ type: 'LB_IMPORT_ITEMS', items });
  toast(sentText(r, `Отправлено: ${Number(r && r.submitted || items.length)}`));
}
// The dock on the right edge (v5.31; the bar in the bottom right corner covered the sites' own buttons): a small tab
// «LB» with the number of chosen cards; the panel slides out of it under the pointer (a click on the tab keeps it
// open): what the program knows of the cards of this page — already there, new, upgrades — and «Отправить выбранные»,
// «Все новые», «Все апгрейды», «Всю страницу», «Снять выбор».  The popup's «Панель на сайтах: не показывать» leaves
// only Alt+click and the context menu.
function ensureToolbar() {
  if ((LB.options.dock || 'tab') === 'off') { document.querySelector('.lb-selection-toolbar')?.remove(); return; }
  if (document.querySelector('.lb-selection-toolbar')) return;
  const dock = document.createElement('div');
  dock.className = 'lb-selection-toolbar lb-dock';
  dock.innerHTML = '<button type="button" class="lb-dock-tab" aria-expanded="false" title="Local Booru — панель">'
    + '<span class="lb-dock-num" hidden>0</span><span class="lb-dock-logo">LB</span></button>'
    + '<div class="lb-dock-panel" role="group" aria-label="Local Booru">'
    + '<div class="lb-dock-head"><b>Local Booru</b><button type="button" class="lb-dock-pin" data-act="pin" title="Не прятать панель" aria-pressed="false">закрепить</button></div>'
    + '<div class="lb-dock-sub" hidden><div class="lb-dock-sub-query"><span>Поиск на сайте</span><b></b></div>'
    + '<button type="button" class="lb-dock-sub-go" data-act="subscribe" hidden>Подписаться</button><div class="lb-dock-sub-note"></div></div>'
    + '<div class="lb-dock-stats"><div class="lb-dock-stat" data-kind="known"><b>0</b><span>уже есть</span></div>'
    + '<div class="lb-dock-stat" data-kind="new"><b>0</b><span>новых</span></div>'
    + '<div class="lb-dock-stat" data-kind="upgrade"><b>0</b><span>апгрейдов</span></div></div>'
    + '<div class="lb-dock-blocked" hidden></div>'
    + '<span class="lb-selected-count" title="Alt+клик по карточке — выбрать, Alt+Shift+клик — выбрать подряд">0 выбрано</span>'
    + '<div class="lb-dock-buttons"><button type="button" data-act="selected" disabled>Отправить выбранные</button>'
    + '<button type="button" data-act="new">Все новые</button><button type="button" data-act="upgrades">Все апгрейды</button>'
    + '<button type="button" data-act="all">Всю страницу</button><button type="button" data-act="clear">Снять выбор</button></div>'
    + '<div class="lb-dock-hint">Alt+клик — выбрать карточку, Alt+Shift+клик — подряд</div></div>';
  const tab = dock.querySelector('.lb-dock-tab');
  let closing = 0;
  const open = (on) => {
    dock.classList.toggle('open', on);
    tab.setAttribute('aria-expanded', on ? 'true' : 'false');
  };
  dock.addEventListener('mouseenter', () => { clearTimeout(closing); open(true); });
  dock.addEventListener('mouseleave', () => {
    clearTimeout(closing);
    if (!dock.classList.contains('pinned')) closing = setTimeout(() => open(false), 450);
  });
  tab.addEventListener('click', () => {
    const pin = !dock.classList.contains('pinned');
    dock.classList.toggle('pinned', pin);
    dock.querySelector('.lb-dock-pin').setAttribute('aria-pressed', pin ? 'true' : 'false');
    open(pin || dock.matches(':hover'));
  });
  dock.addEventListener('click', e => {
    const act = e.target && e.target.closest && e.target.closest('[data-act]') && e.target.closest('[data-act]').dataset.act;
    if (!act) return;
    if (act === 'pin') { tab.click(); return; }
    if (act === 'subscribe') { subscribeHere().catch(err => toast(String(err))); return; }
    if (act === 'clear') { LB.selected.clear(); document.querySelectorAll('.lb-selected').forEach(x => x.classList.remove('lb-selected')); updateCount(); }
    else sendSelected(act).catch(err => toast(String(err)));
  });
  document.documentElement.appendChild(dock);
  updateCount();
  updateStats();
  renderSub();
  updateBlocked();
}
function updateCount() {
  const n = LB.selected.size;
  const el = document.querySelector('.lb-selected-count');
  if (el) el.textContent = n + ' выбрано';
  const num = document.querySelector('.lb-dock-num');
  if (num) { num.textContent = String(n); num.hidden = !n; }
  document.querySelector('.lb-dock')?.classList.toggle('chosen', n > 0);
  const send = document.querySelector('.lb-dock-buttons [data-act="selected"]');
  if (send) { send.disabled = !n; send.textContent = n ? `Отправить выбранные (${n})` : 'Отправить выбранные'; }
}
// what the program knows of the cards of this page (the results of the last checks)
function updateStats() {
  const counts = { known: 0, new: 0, upgrade: 0 };
  for (const r of LB.results.values()) {
    if (r.status === 'downloaded') counts.known += 1;
    else if (r.status === 'upgrade_available') counts.upgrade += 1;
    else counts.new += 1;
  }
  for (const [kind, n] of Object.entries(counts)) {
    const b = document.querySelector(`.lb-dock-stat[data-kind="${kind}"] b`);
    if (b && b.textContent !== String(n)) b.textContent = String(n);
  }
}
// Alt+click selects a card, Alt+Shift+click a run of cards.  Ctrl/⌘/Shift+click stay the browser's
// (open the post in a new tab or window).
function selectionClick(ev) {
  if (!ev.altKey) return;
  const card = ev.target && ev.target.closest ? ev.target.closest('[' + LB.cardAttr + ']') : null;
  if (!card) return;
  ev.preventDefault();
  ev.stopPropagation();
  const cards = [...document.querySelectorAll('[' + LB.cardAttr + ']')];
  const idx = cards.indexOf(card);
  if (ev.shiftKey && LB.lastAnchor >= 0) {
    const a = Math.min(idx, LB.lastAnchor), b = Math.max(idx, LB.lastAnchor);
    for (let i = a; i <= b; i++) { LB.selected.add(cards[i].getAttribute(LB.cardAttr)); cards[i].classList.add('lb-selected'); }
  } else {
    const k = card.getAttribute(LB.cardAttr);
    if (LB.selected.has(k)) { LB.selected.delete(k); card.classList.remove('lb-selected'); }
    else { LB.selected.add(k); card.classList.add('lb-selected'); }
    LB.lastAnchor = idx;
  }
  updateCount();
}

// ── the program's blocklist on the site (0.8.0) ────────────────────────────
// A card with a tag of the blocklist is blurred — its label «Блоклист: …» shows it and hides it again — or hidden;
// the post a page shows is only blurred (hidden, the page would be empty).  A grid card's tags: data-tags (Danbooru,
// e621), its picture's title (Gelbooru, rule34.xxx — with score:/rating:, left out; Moebooru — «… Tags: a b User: …»),
// else the alt, and the tag links in it.
function tagsOfCard(card, item) {
  const out = new Set(((item && item.tags) || []).map(t => String(t).toLowerCase()));
  if (!card || (item && item.is_post_page)) return out;
  const add = text => { for (const part of String(text || '').split(/[\s,]+/)) { const t = normalizeTag(part).toLowerCase(); if (t) out.add(t); } };
  for (const el of scanTargets(card, '[data-tags]')) add(el.getAttribute('data-tags'));
  for (const img of querySafe(card, 'img')) {
    const title = String(img.getAttribute('title') || '');
    const moe = title.match(/\bTags:\s*([\s\S]*?)(?:\s+User:|$)/);
    add(moe ? moe[1] : (title.trim() ? title : img.getAttribute('alt')));
  }
  return out;
}
function markBlocked(card, hits, hide) {
  let label = card.querySelector(':scope > .lb-blocked-label');
  if (!hits.length) {
    card.classList.remove('lb-blocked', 'lb-blocked-hidden', 'lb-blocked-shown');
    if (label) label.remove();
    return;
  }
  card.classList.toggle('lb-blocked-hidden', hide);
  card.classList.toggle('lb-blocked', !hide);
  const text = 'Блоклист: ' + hits.slice(0, 3).join(', ') + (hits.length > 3 ? ` и ещё ${hits.length - 3}` : '');
  if (!label) {
    label = document.createElement('button');
    label.type = 'button';
    label.className = 'lb-blocked-label';
    card.appendChild(label);
  }
  if (label.textContent !== text) label.textContent = text;
  const title = card.classList.contains('lb-blocked-shown') ? 'Размыть снова' : 'Показать';
  if (label.title !== title) label.title = title;
}
function applyBlocklist(items) {
  const mode = LB.options.blocklistMode || 'blur';
  const list = new Set(((LB.options.blocklist || [])).map(t => String(t).toLowerCase()));
  let count = 0;
  for (const item of items) {
    const card = cardByKey(item.key);
    if (!card) continue;
    const hits = mode === 'off' || !list.size ? [] : [...tagsOfCard(card, item)].filter(t => list.has(t)).sort();
    markBlocked(card, hits, mode === 'hide' && !item.is_post_page);
    if (hits.length) count += 1;
  }
  LB.blocked = count;
  updateBlocked();
}
function updateBlocked() {
  const el = document.querySelector('.lb-dock-blocked');
  if (!el) return;
  const text = LB.blocked ? `Блоклист программы: ${(LB.options.blocklistMode || 'blur') === 'hide' ? 'скрыто' : 'размыто'} ${LB.blocked}` : '';
  if (el.textContent !== text) el.textContent = text;
  el.hidden = !LB.blocked;
}
// the label of a blurred card: shows it (and blurs it again); never the site's link under it
function blockedLabelClick(ev) {
  const label = ev.target && ev.target.closest ? ev.target.closest('.lb-blocked-label') : null;
  if (!label) return;
  ev.preventDefault();
  ev.stopPropagation();
  if (ev.type !== 'click') return;
  const card = label.parentElement;
  const shown = card.classList.toggle('lb-blocked-shown');
  label.title = shown ? 'Размыть снова' : 'Показать';
}

// ── «Подписаться» (0.8.0): the search of this page as a subscription of the program ──
// The program reads the site and the search from the address and says what to show: nothing (not a search), why
// not, «Подписка есть», «Добавить этот сайт к подписке» or «Подписаться».  Asked once per address.
function searchOfPage() {
  if (isPostPage()) return '';
  try { return String(new URL(location.href).searchParams.get('tags') || '').trim(); } catch (_) { return ''; }
}
async function subscriptionState() {
  const url = location.href;
  if (!searchOfPage()) { if (LB.sub) { LB.sub = null; renderSub(); } return; }
  if (LB.sub && LB.sub.url === url) return;
  LB.sub = { url, loading: true };
  renderSub();
  let r;
  try { r = await chrome.runtime.sendMessage({ type: 'LB_SUB_STATE', url }); } catch (e) { r = { ok: false, error: String(e && e.message || e) }; }
  if (LB.sub && LB.sub.url === url) { LB.sub = { ...(r || { ok: false }), url }; renderSub(); }
}
function renderSub() {
  const box = document.querySelector('.lb-dock-sub');
  if (!box) return;
  const s = LB.sub;
  if (!s || s.search === false) { box.hidden = true; return; }
  const btn = box.querySelector('.lb-dock-sub-go'), note = box.querySelector('.lb-dock-sub-note');
  box.querySelector('.lb-dock-sub-query b').textContent = s.query || searchOfPage();
  let go = '', say = '';
  const sub = s.subscription || {};
  if (s.loading) say = 'Спрашиваю Local Booru…';
  else if (s.offline) say = 'Local Booru закрыта — подписаться можно, когда она запущена';
  else if (s.ok === false) say = 'Не получилось спросить программу: ' + String(s.message || s.error || 'нет ответа');
  else if (!s.supported) say = 'Подписаться нельзя: ' + String(s.reason || 'этот сайт подписки не листают');
  else if (s.subscribed) say = sub.enabled === false ? 'Подписка есть, но выключена — включить можно на «Подписках»'
    : `Подписка есть: Local Booru проверяет её сама раз в ${Number(sub.every_hours || 24)} ч`;
  else if (s.other_sites) {
    go = 'Добавить этот сайт к подписке';
    say = `Подписка «${String(sub.name || s.query)}» есть на ${(sub.sites || []).join(', ')}`;
  } else {
    go = 'Подписаться';
    say = 'Local Booru будет сама скачивать новые посты этого поиска';
  }
  btn.hidden = !go;
  if (go && btn.textContent !== go) btn.textContent = go;
  btn.disabled = false;
  note.textContent = say;
  box.dataset.state = s.subscribed ? 'subscribed' : (go ? 'can' : 'info');
  box.hidden = false;
}
async function subscribeHere() {
  const url = location.href;
  const btn = document.querySelector('.lb-dock-sub-go');
  if (btn) btn.disabled = true;
  let r;
  try { r = await chrome.runtime.sendMessage({ type: 'LB_SUBSCRIBE', url }); } catch (e) { r = { ok: false, error: String(e && e.message || e) }; }
  if (r && r.ok) {
    LB.sub = { ...r, url };
    toast(String(r.message || 'Подписка добавлена'));
  } else if (r && r.offline) {
    toast('Local Booru закрыта — подписаться можно, когда она запущена');
  } else {
    toast('Не подписано: ' + String(r && (r.message || r.error) || 'программа не ответила'));
  }
  renderSub();
}

async function checkPage() {
  try {
    const o = await chrome.runtime.sendMessage({ type: 'LB_GET_OPTIONS' });
    if (o && o.options) LB.options = o.options;
    LB.profile = findProfile(LB.options.profiles || []);
    if (!LB.options.enabled) return;
    const items = collectItems();
    if (!items.length) return;
    ensureToolbar();
    applyBlocklist(items);
    subscriptionState();
    const resp = await chrome.runtime.sendMessage({ type: 'LB_CHECK_ITEMS', items });
    for (const r of resp && resp.items || []) applyResult(r);
    updateStats();
    if (resp && resp.offline && !LB.offlineNoticed) { LB.offlineNoticed = true; toast('Local Booru закрыта — метки по сохранённому списку'); }
  } catch (e) { /* badge/popup keeps durable error */ }
}
function scheduleCheck() { clearTimeout(LB.pendingTimer); LB.pendingTimer = setTimeout(checkPage, 350); }
async function importContext() {
  const item = itemFromTarget(LB.lastContextTarget);
  if (!item || !item.media_urls.length) { toast('Не найдено медиа'); return; }
  if (canonicalHost() === 'pixiv.net' && item.post_id) {
    const r = await chrome.runtime.sendMessage({ type: 'LB_PIXIV_IMPORT_WORK', illustId: item.post_id });
    toast(sentText(r, 'Pixiv work отправлена: ' + Number(r && r.submitted || 1)));
    return;
  }
  const r = await chrome.runtime.sendMessage({ type: 'LB_IMPORT_ITEM', item });
  toast(sentText(r, r && r.duplicate ? 'Уже в очереди' : 'Отправлено в Local Booru'));
}
function isOwnNode(node) { const el = node && (node.nodeType === 1 ? node : node.parentElement); return !!(el && el.closest && el.closest(OWN_NODES)); }

document.addEventListener('contextmenu', e => { LB.lastContextTarget = e.target; }, true);
document.addEventListener('click', selectionClick, true);
for (const type of ['click', 'mousedown', 'mouseup', 'auxclick']) document.addEventListener(type, openKnown, true);
for (const type of ['click', 'mousedown', 'mouseup', 'auxclick']) document.addEventListener(type, blockedLabelClick, true);
// the popup's «Панель справа» takes effect at once; so do the blocklist's way and a new blocklist from the program
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.dock) {
    LB.options.dock = changes.dock.newValue || 'tab';
    ensureToolbar();
  }
  if (changes.blocklist || changes.blocklistMode) scheduleCheck();
});
chrome.runtime.onMessage.addListener(message => {
  if (!message) return;
  if (message.type === 'LB_CONTEXT_IMPORT_AT_POINT') importContext().catch(e => toast(String(e)));
  if (message.type === 'LB_IMPORT_SELECTED') sendSelected('selected').catch(e => toast(String(e)));
});
scheduleCheck();
setTimeout(() => { chrome.runtime.sendMessage({ type: 'LB_BROWSER_POLL_ONCE' }).catch(() => {}); }, 250);
// Only new page content starts a check; the badges, toasts and toolbar this script adds do not.
LB.observer = new MutationObserver(ms => {
  if (ms.some(m => !isOwnNode(m.target) && [...(m.addedNodes || [])].some(n => !isOwnNode(n)))) scheduleCheck();
});
LB.observer.observe(document.documentElement, { childList: true, subtree: true });
