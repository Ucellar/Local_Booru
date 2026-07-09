'use strict';

const LB = {
  cardAttr: 'data-local-booru-card-id',
  lastContextTarget: null,
  options: { enabled: true, hideMode: 'hide', showBadge: true },
  pendingTimer: null,
  observer: null,
  seq: 1,
};

function host() {
  const h = location.hostname.toLowerCase().replace(/^www\./, '');
  if (h.endsWith('rule34.xxx')) return 'rule34.xxx';
  if (h.endsWith('gelbooru.com')) return 'gelbooru.com';
  if (h === 'danbooru.donmai.us') return 'danbooru.donmai.us';
  if (h === 'e621.net') return 'e621.net';
  if (h === 'e926.net') return 'e926.net';
  if (h === 'booru.allthefallen.moe') return 'booru.allthefallen.moe';
  return h;
}

function absUrl(value) {
  if (!value) return '';
  try { return new URL(value, location.href).href.replace(/\/$/, ''); } catch (_) { return ''; }
}

function md5FromText(text) {
  const m = String(text || '').match(/(?:^|[^0-9a-f])([0-9a-f]{32})(?:[^0-9a-f]|$)/i);
  return m ? m[1].toLowerCase() : '';
}

function postIdFromUrl(url) {
  try {
    const u = new URL(url, location.href);
    const id = u.searchParams.get('id');
    if (id) return id;
    const m = u.pathname.match(/\/posts\/(\d+)/);
    if (m) return m[1];
  } catch (_) {}
  return '';
}

function looksLikePostUrl(url) {
  return /page=post/i.test(url) || /\/posts\/\d+/i.test(url);
}

function closestCard(el) {
  if (!el) return null;
  return el.closest('article, .post-preview, .thumbnail-preview, .thumb, .image-list span, .content span, .post, .preview, li, div') || el;
}

function candidateLinks(root) {
  return Array.from((root || document).querySelectorAll('a[href]')).filter(a => looksLikePostUrl(a.href));
}

function extractItemFromLink(a) {
  const card = closestCard(a);
  if (!card) return null;
  if (!card.getAttribute(LB.cardAttr)) card.setAttribute(LB.cardAttr, 'lb-' + (LB.seq++));
  card.classList.add('lb-companion-card');

  const imgs = Array.from(card.querySelectorAll('img, source'));
  const urls = [a.href];
  for (const img of imgs) {
    for (const attr of ['src', 'data-src', 'data-original', 'data-preview-url', 'data-file-url']) {
      const v = img.getAttribute(attr);
      if (v) urls.push(absUrl(v));
    }
    if (img.src) urls.push(absUrl(img.src));
    if (img.srcset) {
      for (const part of img.srcset.split(',')) urls.push(absUrl(part.trim().split(/\s+/)[0]));
    }
  }
  for (const attr of ['data-md5', 'data-file-md5', 'data-hash', 'data-file-url', 'data-large-file-url', 'data-preview-file-url']) {
    const v = card.getAttribute(attr) || a.getAttribute(attr);
    if (v) urls.push(v);
  }

  const postUrl = absUrl(a.href);
  let fileUrl = '';
  for (const u of urls) {
    const au = absUrl(u);
    if (au && au !== postUrl && /\.(?:jpe?g|png|gif|webp|avif|mp4|webm)(?:[?#].*)?$/i.test(au)) { fileUrl = au; break; }
  }
  let md5 = '';
  for (const v of urls.concat([card.outerHTML.slice(0, 2000)])) {
    md5 = md5FromText(v);
    if (md5) break;
  }
  const explicitId = card.getAttribute('data-id') || card.getAttribute('data-post-id') || a.getAttribute('data-id') || '';
  const postId = explicitId || postIdFromUrl(postUrl);
  return {
    key: card.getAttribute(LB.cardAttr),
    site: host(),
    post_id: postId,
    post_url: postUrl,
    file_url: fileUrl,
    md5,
  };
}

function collectItems() {
  const seen = new Set();
  const out = [];
  for (const a of candidateLinks(document)) {
    const item = extractItemFromLink(a);
    if (!item || !item.post_url) continue;
    const k = item.md5 || item.site + ':' + item.post_id || item.post_url;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(item);
    if (out.length >= 250) break;
  }
  return out;
}

function cardByKey(key) {
  if (!key) return null;
  return document.querySelector('[' + LB.cardAttr + '="' + CSS.escape(key) + '"]');
}

function addBadge(card, text) {
  if (!LB.options.showBadge || !card) return;
  let badge = card.querySelector(':scope > .lb-known-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.className = 'lb-known-badge';
    card.appendChild(badge);
  }
  badge.textContent = text;
}

function applyResult(result) {
  if (!result || !result.key) return;
  const card = cardByKey(result.key);
  if (!card) return;
  if (result.action !== 'hide') return;
  const mode = LB.options.hideMode || 'hide';
  card.dataset.localBooruStatus = result.status || 'known';
  card.dataset.localBooruReason = result.reason || '';
  addBadge(card, result.status === 'hidden' ? 'Скрыто' : 'Уже есть');
  if (mode === 'dim') card.classList.add('lb-known-dim');
  else card.classList.add('lb-known-hidden');
}

function toast(text) {
  const old = document.querySelector('.lb-companion-toast');
  if (old) old.remove();
  const el = document.createElement('div');
  el.className = 'lb-companion-toast';
  el.textContent = text;
  document.documentElement.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

async function checkPage() {
  try {
    const optResp = await chrome.runtime.sendMessage({ type: 'LB_GET_OPTIONS' });
    if (optResp && optResp.options) LB.options = optResp.options;
    if (!LB.options.enabled) return;
    const items = collectItems();
    if (!items.length) return;
    const resp = await chrome.runtime.sendMessage({ type: 'LB_CHECK_ITEMS', items });
    if (!resp || resp.ok === false || resp.disabled) return;
    for (const r of (resp.items || [])) applyResult(r);
  } catch (err) {
    // Local Booru may be closed. Fail silently on pages; popup shows status.
  }
}

function scheduleCheck() {
  clearTimeout(LB.pendingTimer);
  LB.pendingTimer = setTimeout(checkPage, 250);
}

function itemFromCard(card) {
  if (!card) return null;
  const a = card.querySelector('a[href]');
  return a ? extractItemFromLink(a) : null;
}

async function hideCard(card) {
  const item = itemFromCard(card);
  if (!item) { toast('Не нашёл карточку Local Booru'); return; }
  try {
    await chrome.runtime.sendMessage({ type: 'LB_HIDE_ITEM', item, query: new URLSearchParams(location.search).get('tags') || '' });
    card.classList.add('lb-known-hidden');
    toast('Скрыто в граббере/браузере. Парсер не заблокирован.');
  } catch (err) {
    toast('Local Booru API не ответил: ' + String(err && err.message || err));
  }
}

document.addEventListener('contextmenu', (ev) => {
  LB.lastContextTarget = ev.target;
}, true);

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.type !== 'LB_CONTEXT_HIDE_AT_POINT') return;
  const card = closestCard(LB.lastContextTarget);
  hideCard(card);
});

scheduleCheck();
LB.observer = new MutationObserver((mutations) => {
  for (const m of mutations) {
    if (m.addedNodes && m.addedNodes.length) { scheduleCheck(); break; }
  }
});
LB.observer.observe(document.documentElement, { childList: true, subtree: true });

// Browser API bridges: execute JSON fetches inside the user's already-open
// verified page context. Local Booru never launches chrome.exe for these.
if (host() === 'e621.net' || host() === 'e926.net') {
  setInterval(() => {
    chrome.runtime.sendMessage({ type: 'LB_E621_POLL_ONCE' }).catch(() => {});
  }, 1500);
}
// Pixiv source->MD5 relay can be awakened from any supported companion page.
// The service worker will use a real Pixiv tab if one exists, otherwise it will
// use extension-worker fetch with pixiv.net host permissions. This avoids the
// old v344 failure where Local Booru waited for a Pixiv tab and timed out.
setInterval(() => {
  chrome.runtime.sendMessage({ type: 'LB_PIXIV_POLL_ONCE' }).catch(() => {});
}, 1500);
