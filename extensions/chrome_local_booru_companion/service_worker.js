'use strict';

const API_BASE_DEFAULT = 'http://127.0.0.1:47734';
const STORAGE = chrome.storage.local;
const DEFAULT_OPTIONS = {
  enabled: true,
  hideMode: 'hide',
  showBadge: true,
  dock: 'tab',                       // the panel on the right edge of the sites (v5.31): 'tab' — slides out, 'off' — none
  apiBase: API_BASE_DEFAULT,
  e621BridgeEnabled: true,
  pixivBridgeEnabled: true,
  browserResolverEnabled: true,
  pairingToken: '',
  extensionId: '',
  profileRevision: 0,
  profiles: [],
  lastError: '',
  arbitraryOrigins: [],
  blocklistMode: 'blur',             // (0.8.0) the program's blocklist on the sites: 'blur', 'hide' or 'off'
  blocklist: [],                     // its tags, as the program last gave them (kept while it is closed)
  blocklistRev: '',
  blocklistSyncedAt: 0,
  mangaMode: 'blur',                 // (0.9.0) nhentai: the galleries the manga folder has — 'blur', 'hide', 'mark', 'off'
  mangaVersions: true,               // … and the other versions of their works (another language, another upload)
  mangaSignin: true,                 // (0.10.0) the sign-in on the program's sites of manga goes to the program
};
const WATCHDOG_ALARM = 'lb-companion-event-watchdog';
let eventLoopRunning = false;

const BROWSER_WORKER_TAB_KEY = 'browserBridgeWorkerTabId';
const LEGACY_EXTERNAL_WORKER_TAB_KEY = 'externalBrowserWorkerTabId';
const BROWSER_WORKER_IDLE_STATE_KEY = 'browserBridgeWorkerIdleState';
const BROWSER_WORKER_IDLE_ALARM = 'lb-companion-worker-idle-close';
const BROWSER_WORKER_IDLE_MS = 60000;
let lbBrowserWorkerTabId = 0;
let lbBrowserWorkerCloseTimer = null;
let lbBrowserWorkerInUse = 0;
let lbBrowserBridgeMutex = Promise.resolve();

async function migrateStorageOnce() {
  // Only the flag: the whole storage now holds the offline index (megabytes) and this runs per request.
  const flag = await STORAGE.get({ storageMigratedV460: false });
  if (flag.storageMigratedV460) return;
  const local = await STORAGE.get(null);
  let old = {};
  try { old = await chrome.storage.sync.get(null); } catch (_) {}
  const merged = { ...old, ...local, storageMigratedV460: true };
  await STORAGE.set(merged);
  try { if (Object.keys(old).length) await chrome.storage.sync.clear(); } catch (_) {}
}

async function getOptions() {
  await migrateStorageOnce();
  const data = await STORAGE.get(DEFAULT_OPTIONS);
  return { ...DEFAULT_OPTIONS, ...data };
}

async function setFailure(message) {
  const text = String(message || '').slice(0, 500);
  await STORAGE.set({ lastError: text });
  try { await chrome.action.setBadgeText({ text: '!' }); await chrome.action.setBadgeBackgroundColor({ color: '#b3261e' }); } catch (_) {}
}

async function clearFailure() {
  await STORAGE.set({ lastError: '' });
  try { await chrome.action.setBadgeText({ text: '' }); } catch (_) {}
}

async function apiFetch(path, payload, { allowUnpaired = false, quiet = false } = {}) {
  const options = await getOptions();
  const base = String(options.apiBase || API_BASE_DEFAULT).replace(/\/$/, '');
  const headers = { 'Content-Type': 'application/json', 'X-Local-Booru-Companion': '1' };
  if (options.pairingToken && !allowUnpaired) headers['X-Local-Booru-Pairing'] = options.pairingToken;
  const res = await fetch(base + path, { method: 'POST', headers, body: JSON.stringify(payload || {}), credentials: 'omit', cache: 'no-store' });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { ok: false, error: 'bad_json', raw: text.slice(0, 200) }; }
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || ('HTTP ' + res.status)); err.payload = data;
    if (!quiet) await setFailure(err.message);   // (0.10.0) quiet: a call in the background a program before 5.44.100 lacks
    throw err;
  }
  await clearFailure();
  return data;
}

async function apiFetchResultWithRetry(path, payload) {
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      return await apiFetch(path, payload);
    } catch (err) {
      lastError = err;
      const message = String(err && err.message || err);
      if (/pairing_required|bad_task_token|forbidden/i.test(message)) break;
      if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
  throw lastError || new Error('result_submit_failed');
}

async function pairCompanion(code, displayName) {
  const result = await apiFetch('/extension/pair', { code, display_name: displayName || 'Local Booru Companion' }, { allowUnpaired: true });
  await STORAGE.set({ pairingToken: result.pairing_token || '', extensionId: result.extension_id || '' });
  return result;
}

async function refreshProfiles() {
  const result = await apiFetch('/extension/profiles', {});
  const profiles = Array.isArray(result.profiles) ? result.profiles : [];
  await STORAGE.set({ profiles, profileRevision: Number(result.revision || 0) });
  return profiles;
}

// ── offline mode ───────────────────────────────────────────────────────────
// While Local Booru is closed the extension still marks cards «Уже есть» by its own copy of what
// the library has (MD5 of cards and deleted files, booru post ids), and «Скачать» keeps the posts
// in a queue that goes to the program as soon as it answers again.
const OFFLINE_INDEX_KEY = 'offlineIndex';
const OFFLINE_INDEX_META_KEY = 'offlineIndexMeta';
const OFFLINE_QUEUE_KEY = 'offlineQueue';
const OFFLINE_QUEUE_MAX = 5000;
const INDEX_SYNC_EVERY_MS = 10 * 60 * 1000;
const INDEX_FULL_EVERY_MS = 24 * 60 * 60 * 1000;
const MD5_IN_TEXT = /(?:^|[^0-9a-f])([0-9a-f]{32})(?:[^0-9a-f]|$)/i;
const SITE_ALIASES = {
  'api.rule34.xxx': 'rule34.xxx', 'api-cdn.rule34.xxx': 'rule34.xxx', 'wimg.rule34.xxx': 'rule34.xxx',
  'img2.gelbooru.com': 'gelbooru.com', 'cdn.donmai.us': 'danbooru.donmai.us',
  'static1.e621.net': 'e621.net', 'static1.e926.net': 'e926.net',
};
let offlineSets = null;
let indexSyncPromise = null;
let queueFlushPromise = null;

function isOfflineError(err) {
  // fetch() itself fails when nothing listens on the program's port; 503 — the program is starting.
  const text = String(err && err.message || err || '');
  return (err && err.name === 'TypeError') || /Failed to fetch|NetworkError|Load failed|browser_import_not_ready|browser_companion_disabled/i.test(text);
}

function normalizeSite(value) {
  let host = String(value || '').trim().toLowerCase();
  if (host.includes('://')) { try { host = new URL(host).hostname.toLowerCase(); } catch (_) { return ''; } }
  host = host.replace(/^www\./, '');
  if (host.endsWith('.rule34.xxx')) host = SITE_ALIASES[host] || 'rule34.xxx';
  return SITE_ALIASES[host] || host;
}

function postIdOf(url) {
  try {
    const u = new URL(String(url || ''));
    const id = u.searchParams.get('id');
    if (id && /^\d+$/.test(id)) return id;
    const m = u.pathname.match(/\/(?:posts|post\/show|artworks)\/(\d+)/);
    return m ? m[1] : '';
  } catch (_) { return ''; }
}

function md5Chunks(text) {
  const out = [];
  const value = String(text || '');
  for (let i = 0; i + 32 <= value.length; i += 32) out.push(value.slice(i, i + 32));
  return out;
}

async function loadOfflineSets() {
  if (offlineSets) return offlineSets;
  const index = (await STORAGE.get({ [OFFLINE_INDEX_KEY]: null }))[OFFLINE_INDEX_KEY];
  if (!index || !index.cursor) return null;
  const posts = new Map();
  for (const [site, ids] of Object.entries(index.posts || {})) posts.set(site, new Set((ids || []).map(String)));
  offlineSets = { have: new Set(md5Chunks(index.md5)), deleted: new Set(md5Chunks(index.deleted)), posts };
  return offlineSets;
}

function offlineCheckItems(sets, items) {
  return items.map((item) => {
    const site = normalizeSite(item.site || item.post_url || item.file_url);
    const md5 = String(item.md5 || '').toLowerCase().match(/^[0-9a-f]{32}$/) ? String(item.md5).toLowerCase()
      : ((String(item.file_url || '').match(MD5_IN_TEXT) || String(item.post_url || '').match(MD5_IN_TEXT) || [])[1] || '').toLowerCase();
    const postId = String(item.post_id || '') || postIdOf(item.post_url);
    const base = { key: String(item.key || ''), site, post_id: postId, post_url: String(item.post_url || ''), md5, offline: true };
    if (md5 && sets.have.has(md5)) return { ...base, status: 'downloaded', action: 'hide', reason: 'offline_md5' };
    if (md5 && sets.deleted.has(md5)) return { ...base, status: 'deleted_rule', action: 'hide', reason: 'offline_deleted' };
    const ids = sets.posts.get(site);
    if (postId && ids && ids.has(String(postId))) return { ...base, status: 'downloaded', action: 'hide', reason: 'offline_post' };
    return { ...base, status: 'new', action: 'show', reason: 'offline_unknown' };
  });
}

async function syncOfflineIndex({ force = false } = {}) {
  if (indexSyncPromise) return indexSyncPromise;
  indexSyncPromise = (async () => {
    const meta = (await STORAGE.get({ [OFFLINE_INDEX_META_KEY]: {} }))[OFFLINE_INDEX_META_KEY] || {};
    const now = Date.now();
    if (!force && meta.syncedAt && now - Number(meta.syncedAt) < INDEX_SYNC_EVERY_MS) return meta;
    const stored = (await STORAGE.get({ [OFFLINE_INDEX_KEY]: null }))[OFFLINE_INDEX_KEY];
    const full = !stored || !stored.cursor || now - Number(stored.fullAt || 0) > INDEX_FULL_EVERY_MS;
    const res = await apiFetch('/extension/index', { cursor: full ? null : stored.cursor });
    let index;
    if (res.full || full) {
      index = { md5: String(res.md5 || ''), deleted: String(res.deleted || ''), posts: res.posts || {}, cursor: res.cursor, fullAt: now };
    } else {
      const undeleted = new Set(md5Chunks(res.undeleted));
      const deleted = md5Chunks(stored.deleted).filter((m) => !undeleted.has(m)).join('') + String(res.deleted || '');
      const posts = { ...(stored.posts || {}) };
      for (const [site, ids] of Object.entries(res.posts || {})) posts[site] = [...(posts[site] || []), ...(ids || [])];
      index = { md5: String(stored.md5 || '') + String(res.md5 || ''), deleted, posts, cursor: res.cursor, fullAt: stored.fullAt };
    }
    const count = Math.floor(index.md5.length / 32);
    const newMeta = { syncedAt: now, count, fullAt: index.fullAt };
    await STORAGE.set({ [OFFLINE_INDEX_KEY]: index, [OFFLINE_INDEX_META_KEY]: newMeta });
    offlineSets = null;
    return newMeta;
  })().finally(() => { indexSyncPromise = null; });
  return indexSyncPromise;
}

function queueKey(item) {
  const md5 = String(item && item.md5 || '').toLowerCase();
  return md5 || (normalizeSite(item && item.site) + ':' + String(item && item.post_id || '')) + '|' + String(item && (item.post_url || item.file_url) || '');
}

async function showQueueBadge(count) {
  try {
    if (count > 0) {
      await chrome.action.setBadgeText({ text: String(Math.min(count, 999)) });
      await chrome.action.setBadgeBackgroundColor({ color: '#1565c0' });
    } else {
      const { lastError } = await STORAGE.get({ lastError: '' });
      if (!lastError) await chrome.action.setBadgeText({ text: '' });
    }
  } catch (_) {}
}

async function queueOffline(items) {
  const queue = (await STORAGE.get({ [OFFLINE_QUEUE_KEY]: [] }))[OFFLINE_QUEUE_KEY] || [];
  const known = new Set(queue.map(queueKey));
  let added = 0;
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const key = queueKey(item);
    if (known.has(key)) continue;
    known.add(key);
    queue.push({ ...item, queued_offline_at: Date.now() });
    added += 1;
  }
  const kept = queue.slice(-OFFLINE_QUEUE_MAX);
  await STORAGE.set({ [OFFLINE_QUEUE_KEY]: kept });
  await showQueueBadge(kept.length);
  return { added, total: kept.length };
}

async function flushOfflineQueue() {
  if (queueFlushPromise) return queueFlushPromise;
  queueFlushPromise = (async () => {
    let sent = 0;
    while (true) {
      const queue = (await STORAGE.get({ [OFFLINE_QUEUE_KEY]: [] }))[OFFLINE_QUEUE_KEY] || [];
      if (!queue.length) break;
      const chunk = queue.slice(0, 100).map(({ queued_offline_at, ...item }) => item);
      await apiFetch('/extension/import_batch', { items: chunk });     // still offline: stays queued
      const rest = ((await STORAGE.get({ [OFFLINE_QUEUE_KEY]: [] }))[OFFLINE_QUEUE_KEY] || []).slice(chunk.length);
      await STORAGE.set({ [OFFLINE_QUEUE_KEY]: rest });
      sent += chunk.length;
    }
    await showQueueBadge(0);
    return sent;
  })().finally(() => { queueFlushPromise = null; });
  return queueFlushPromise;
}

async function importOrQueue(path, payload, items) {
  try {
    const result = await apiFetch(path, payload);
    flushOfflineQueue().catch(() => {});
    return result;
  } catch (err) {
    if (!isOfflineError(err)) throw err;
    const queued = await queueOffline(items);
    return { ok: true, queued_offline: true, submitted: items.length, added: queued.added, offline_queue: queued.total };
  }
}

async function offlineStatus() {
  const meta = (await STORAGE.get({ [OFFLINE_INDEX_META_KEY]: {} }))[OFFLINE_INDEX_META_KEY] || {};
  const queue = (await STORAGE.get({ [OFFLINE_QUEUE_KEY]: [] }))[OFFLINE_QUEUE_KEY] || [];
  const { blocklist } = await STORAGE.get({ blocklist: [] });
  return { ok: true, index_count: Number(meta.count || 0), index_synced_at: Number(meta.syncedAt || 0), queue: queue.length,
           blocklist_count: Array.isArray(blocklist) ? blocklist.length : 0 };
}

// ── the program's blocklist (0.8.0) ────────────────────────────────────────
// Cards with its tags are blurred or hidden on the sites — also while the program is closed, by the copy kept here.
// A blocklist saved in the program wakes the long poll (status.blocklist_rev): the new one is taken at once; pages
// that ask for the options take it at most once a minute besides.
const BLOCKLIST_SYNC_EVERY_MS = 60 * 1000;
let blocklistSyncPromise = null;

async function syncBlocklist({ force = false } = {}) {
  if (blocklistSyncPromise) return blocklistSyncPromise;
  blocklistSyncPromise = (async () => {
    const stored = await STORAGE.get({ blocklist: [], blocklistSyncedAt: 0 });
    if (!force && Date.now() - Number(stored.blocklistSyncedAt || 0) < BLOCKLIST_SYNC_EVERY_MS) return stored.blocklist;
    const res = await apiFetch('/extension/blocklist', {});
    const tags = (Array.isArray(res.tags) ? res.tags : []).map(t => String(t).toLowerCase()).filter(Boolean).slice(0, 5000);
    const patch = { blocklistSyncedAt: Date.now(), blocklistRev: String(res.rev || '') };
    // the same list again: no change of «blocklist», so the open tabs do not check their pages again
    if (JSON.stringify(stored.blocklist || []) !== JSON.stringify(tags)) patch.blocklist = tags;
    await STORAGE.set(patch);
    return tags;
  })().finally(() => { blocklistSyncPromise = null; });
  return blocklistSyncPromise;
}

// ── «Подписаться» (0.8.0): the search of the page as a subscription of the program ──
// The program reads the site and the search from the page's address; while it is closed nothing is promised.
async function subscriptionCall(path, url) {
  try {
    return await apiFetch(path, { url: String(url || '').slice(0, 2000) });
  } catch (err) {
    if (isOfflineError(err)) return { ok: false, offline: true, error: 'offline' };
    return { ok: false, ...(err && err.payload || {}), error: String(err && err.message || err) };
  }
}

// (0.11.0) «Скачать избранное»: what the program knows of an account's favorites; the post numbers the page collected on
// the site (the program downloads what the library lacks and parses it).  The question is quiet: a program before 5.44.141
// lacks it, and an account's page must not leave a failure on the extension's badge.
async function favoritesCall(path, message) {
  const payload = { site: String(message.site || '').slice(0, 100), account: String(message.account || '').slice(0, 40) };
  if (path === '/extension/favorites') {
    payload.name = String(message.name || '').slice(0, 200);
    payload.ids = (Array.isArray(message.ids) ? message.ids : []).map(Number).filter(n => Number.isInteger(n) && n > 0).slice(0, 200000);
    payload.mode = message.mode === 'new' ? 'new' : 'all';
  }
  try {
    return await apiFetch(path, payload, { quiet: path !== '/extension/favorites' });
  } catch (err) {
    if (isOfflineError(err)) return { ok: false, offline: true, error: 'offline' };
    return { ok: false, ...(err && err.payload || {}), error: String(err && err.message || err) };
  }
}



chrome.runtime.onInstalled.addListener(async () => {
  await migrateStorageOnce();
  try { await chrome.contextMenus.removeAll(); } catch (_) {}
  chrome.contextMenus.create({ id: 'lb-download-to-local-booru', title: 'Скачать в Local Booru', contexts: ['all'] });
  chrome.contextMenus.create({ id: 'lb-download-selected', title: 'Отправить выбранные в Local Booru', contexts: ['page'] });
  try { chrome.alarms.create(WATCHDOG_ALARM, { periodInMinutes: 1 }); } catch (_) {}
  await reconcileBrowserWorkerState().catch(() => {});
  startEventLoop();
});
chrome.runtime.onStartup.addListener(() => {
  try { chrome.alarms.create(WATCHDOG_ALARM, { periodInMinutes: 1 }); } catch (_) {}
  reconcileBrowserWorkerState().catch(() => {}).finally(() => startEventLoop());
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm) return;
  if (alarm.name === WATCHDOG_ALARM) startEventLoop();
  if (alarm.name === BROWSER_WORKER_IDLE_ALARM) browserWorkerHandleIdleAlarm().catch(() => {});
});
chrome.tabs.onRemoved.addListener((tabId) => { browserWorkerHandleTabRemoved(tabId).catch(() => {}); });
reconcileBrowserWorkerState().catch(() => {}).finally(() => startEventLoop());

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab || !tab.id) return;
  const type = info.menuItemId === 'lb-download-selected' ? 'LB_IMPORT_SELECTED' :
    (info.menuItemId === 'lb-download-to-local-booru' ? 'LB_CONTEXT_IMPORT_AT_POINT' : '');
  if (!type) return;
  chrome.tabs.sendMessage(tab.id, { type }).catch(err => setFailure('context_menu:' + String(err && err.message || err)));
});


async function e621FetchInPage(tabId, task) {
  if (!tabId) throw new Error('missing_e621_tab_id');
  const injected = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [{ url: task.url, authorization: task.authorization || '' }],
    func: async (payload) => {
      const headers = { 'Accept': 'application/json' };
      if (payload.authorization) headers['Authorization'] = payload.authorization;
      const resp = await fetch(payload.url, {
        method: 'GET',
        headers,
        credentials: 'include',
        cache: 'no-store',
      });
      const text = await resp.text();
      const outHeaders = {};
      try { resp.headers.forEach((v, k) => { outHeaders[k] = v; }); } catch (_) {}
      const pageText = String(document.body && document.body.innerText || '').slice(0, 1200);
      return {
        status: resp.status,
        url: resp.url,
        headers: outHeaders,
        text: text.slice(0, 4500000),
        bridge_mode: 'page_main_world_fetch',
        page_url: location.href,
        page_title: document.title || '',
        page_probe: pageText,
      };
    },
  });
  const first = Array.isArray(injected) && injected.length ? injected[0] : null;
  if (!first || !first.result) throw new Error('page_fetch_no_result');
  return first.result;
}

async function e621FetchFromExtensionWorker(task) {
  const headers = { 'Accept': 'application/json' };
  if (task.authorization) headers['Authorization'] = task.authorization;
  const res = await fetch(task.url, {
    method: 'GET',
    headers,
    credentials: 'include',
    cache: 'no-store',
  });
  const text = await res.text();
  const outHeaders = {};
  try { res.headers.forEach((v, k) => { outHeaders[k] = v; }); } catch (_) {}
  return {
    status: res.status,
    url: res.url,
    headers: outHeaders,
    text: text.slice(0, 4500000),
    bridge_mode: 'extension_worker_fetch_fallback',
  };
}


async function pixivDocumentHtmlFromPage(tabId) {
  if (!tabId) throw new Error('missing_pixiv_tab_id');
  const injected = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: () => {
      const text = String(document.documentElement && document.documentElement.outerHTML || '');
      return {
        status: 200,
        url: location.href,
        headers: { 'content-type': 'text/html; charset=utf-8' },
        text: text.slice(0, 4500000),
        bridge_mode: 'pixiv_target_tab_document_html',
        page_url: location.href,
        page_title: document.title || '',
      };
    },
  });
  const first = Array.isArray(injected) && injected.length ? injected[0] : null;
  if (!first || !first.result) throw new Error('pixiv_document_html_no_result');
  return first.result;
}


async function pixivFetchInPage(tabId, task) {
  if (!tabId) throw new Error('missing_pixiv_tab_id');
  const injected = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [{ url: task.url, referer: task.referer || '' }],
    func: async (payload) => {
      let pageUserId = '';
      try {
        const p = window.pixiv || {};
        const u = p.user || {};
        pageUserId = String(u.id || u.userId || '');
      } catch (_) {}
      const headers = {
        'Accept': 'application/json, text/plain, */*',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin': 'https://www.pixiv.net',
      };
      if (/^\d+$/.test(pageUserId)) headers['X-User-Id'] = pageUserId;
      if (payload.referer || location.href) headers['Referer'] = payload.referer || location.href;
      const resp = await fetch(payload.url, {
        method: 'GET',
        headers,
        credentials: 'include',
        cache: 'no-store',
        referrer: payload.referer || location.href,
      });
      const text = await resp.text();
      const outHeaders = {};
      try { resp.headers.forEach((v, k) => { outHeaders[k] = v; }); } catch (_) {}
      return {
        status: resp.status,
        url: resp.url,
        headers: outHeaders,
        text: text.slice(0, 4500000),
        bridge_mode: 'pixiv_page_main_world_fetch',
        page_url: location.href,
        page_title: document.title || '',
        page_user_id_present: /^\d+$/.test(pageUserId),
      };
    },
  });
  const first = Array.isArray(injected) && injected.length ? injected[0] : null;
  if (!first || !first.result) throw new Error('pixiv_page_fetch_no_result');
  return first.result;
}

function pixivArtworkIdFromTask(task) {
  const joined = String((task && task.url) || '') + ' ' + String((task && task.referer) || '');
  let m = joined.match(/\/artworks\/(\d{5,})/);
  if (m && m[1]) return m[1];
  m = joined.match(/\/ajax\/illust\/(\d{5,})/);
  if (m && m[1]) return m[1];
  m = joined.match(/[?&]illust_id=(\d{5,})/);
  if (m && m[1]) return m[1];
  return '';
}

function waitForTabComplete(tabId, timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try { chrome.tabs.onUpdated.removeListener(listener); } catch (_) {}
      resolve();
    };
    const listener = (id, info) => {
      if (id === tabId && info && info.status === 'complete') finish();
    };
    try { chrome.tabs.onUpdated.addListener(listener); } catch (_) {}
    setTimeout(finish, timeoutMs || 9000);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab && tab.status === 'complete') setTimeout(finish, 350);
    }).catch(() => {});
  });
}

async function pixivFetchFromTargetArtworkTab(task) {
  const artworkId = pixivArtworkIdFromTask(task);
  if (!artworkId) throw new Error('missing_pixiv_artwork_id_for_target_tab');
  const targetUrl = `https://www.pixiv.net/en/artworks/${artworkId}`;
  const opened = await browserWorkerOpenUrl(targetUrl);
  const tabId = opened.tabId;
  try {
    await waitForTabComplete(tabId, 11000);
    // Give Pixiv SPA a moment to hydrate meta/preload/user state.
    await new Promise(r => setTimeout(r, 1400));

    const isArtworkHtmlRequest = /\/artworks\/\d{5,}/.test(String((task && task.url) || ''));
    let result = null;

    if (isArtworkHtmlRequest) {
      result = await pixivDocumentHtmlFromPage(tabId);
      result.bridge_mode = 'pixiv_shared_worker_document_html';
    } else {
      result = await pixivFetchInPage(tabId, task);
      result.bridge_mode = 'pixiv_shared_worker_main_world_fetch';
      if (result && (result.status === 404 || result.status === 403 || result.status === 401)) {
        try {
          const htmlResult = await pixivDocumentHtmlFromPage(tabId);
          htmlResult.bridge_mode = 'pixiv_shared_worker_document_html_after_ajax_' + result.status;
          htmlResult.page_fetch_error = 'shared_worker_html_after_ajax_status=' + result.status + '; target=' + targetUrl + '; ajax_mode=' + (result.bridge_mode || '');
          result = htmlResult;
        } catch (htmlErr) {
          result.page_fetch_error = 'shared_worker_ajax_status=' + result.status + '; target_html_error=' + String(htmlErr && htmlErr.message || htmlErr).slice(0, 120) + '; target=' + targetUrl;
        }
      }
    }

    result.page_fetch_error = (result.page_fetch_error || 'shared_worker_target:' + targetUrl);
    result.worker_tab_id = tabId;
    return result;
  } catch (err) {
    await browserWorkerCloseManagedTab(tabId);
    throw err;
  }
}

async function getPixivUserId() {
  // Pixiv AJAX can return a masked 404 when X-User-Id is missing.  Read the
  // identifier from the browser cookie jar; never inspect an ordinary user tab.
  try {
    if (chrome.cookies && chrome.cookies.get) {
      const c = await chrome.cookies.get({ url: 'https://www.pixiv.net/', name: 'PHPSESSID' });
      const raw = String((c && c.value) || '');
      const m = raw.match(/^(\d+)_/);
      if (m && m[1]) return { id: m[1], source: 'PHPSESSID' };
    }
  } catch (_) {}
  return { id: '', source: '' };
}

async function pixivFetchFromExtensionWorker(task) {
  const user = await getPixivUserId();
  const headers = {
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': task.referer || 'https://www.pixiv.net/',
    'Origin': 'https://www.pixiv.net',
  };
  if (user.id) headers['X-User-Id'] = user.id;
  const res = await fetch(task.url, {
    method: 'GET',
    headers,
    credentials: 'include',
    cache: 'no-store',
    redirect: 'follow',
    referrer: task.referer || 'https://www.pixiv.net/',
  });
  const text = await res.text();
  const outHeaders = {};
  try { res.headers.forEach((v, k) => { outHeaders[k] = v; }); } catch (_) {}
  return {
    status: res.status,
    url: res.url,
    headers: outHeaders,
    text: text.slice(0, 4500000),
    bridge_mode: 'pixiv_extension_worker_fetch_fallback',
    pixiv_user_id_source: user.source || '',
    pixiv_user_id_present: !!user.id,
  };
}

async function pollPixivBridgeOnceUnlocked(senderTab) {
  const options = await getOptions();
  if (!options.enabled || !options.pixivBridgeEnabled) return { ok: true, skipped: true };
  const next = await apiFetch('/extension/pixiv/next', {});
  if (!next || !next.has_task || !next.task) return { ok: true, idle: true };
  const task = next.task;
  let managedWorkerTabId = 0;
  try {
    // v353: Pixiv source-MD5 donor must not depend on whichever Pixiv tab happens
    // to be open.  In real runs a stale/deleted Pixiv tab can make MAIN-world fetch
    // return 404 and hide the extension-worker result.  Use the MV3 service-worker
    // fetch first; it uses the normal browser cookie jar via host_permissions and
    // still never launches chrome.exe.  Keep page metadata only for diagnostics.
    let result = await pixivFetchFromExtensionWorker(task);
    result.bridge_mode = 'pixiv_extension_worker_fetch_forced';
    result.page_fetch_error = 'ordinary_tabs_not_used; user_id=' + (result.pixiv_user_id_present ? 'yes:' + (result.pixiv_user_id_source || 'unknown') : 'no');
    // If the cookie-backed worker fetch is masked as 404/403/401, retry in
    // the single managed browser-bridge worker tab on the exact artwork page.
    // Ordinary Pixiv tabs are never inspected, adopted, or redirected.
    if (result && (result.status === 404 || result.status === 403 || result.status === 401)) {
      try {
        const workerStatus = result.status;
        const workerMode = result.bridge_mode || '';
        const workerNote = result.page_fetch_error || '';
        const tabResult = await pixivFetchFromTargetArtworkTab(task);
        if (tabResult) {
          managedWorkerTabId = Number(tabResult.worker_tab_id || 0);
          // v356: always surface the target-artwork-tab result.  In v355 a target
          // tab that also returned 404 was hidden behind the worker result, so the
          // Local Booru log could not prove whether the target tab fallback ran.
          tabResult.page_fetch_error = 'target_retry_after_worker_status=' + workerStatus + '; target_mode=' + (tabResult.bridge_mode || '') + '; worker_mode=' + workerMode + '; ' + (tabResult.page_fetch_error || '') + '; worker_note=' + workerNote;
          result = tabResult;
        }
      } catch (targetErr) {
        result.page_fetch_error = 'target_tab_error=' + String(targetErr && targetErr.message || targetErr).slice(0, 180) + '; ' + (result.page_fetch_error || '');
      }
    }
    await apiFetchResultWithRetry('/extension/pixiv/result', {
      id: task.id,
      task_token: task.task_token || '',
      status: result.status,
      url: result.url,
      headers: result.headers || {},
      text: result.text || '',
      bridge_mode: result.bridge_mode || '',
      page_url: '',
      page_title: '',
      page_fetch_error: result.page_fetch_error || '',
    });
    if (managedWorkerTabId) await browserWorkerRetireAfterSuccess(managedWorkerTabId);
    return { ok: true, status: result.status, bridge_mode: result.bridge_mode || '' };
  } catch (err) {
    if (managedWorkerTabId) await browserWorkerCloseManagedTab(managedWorkerTabId);
    await apiFetchResultWithRetry('/extension/pixiv/result', {
      id: task.id,
      task_token: task.task_token || '',
      status: 0,
      url: task.url,
      headers: {},
      text: '',
      error: String(err && err.message || err),
      bridge_mode: 'failed',
    }).catch(() => {});
    return { ok: false, error: String(err && err.message || err) };
  }
}

async function pollE621BridgeOnceUnlocked(senderTab) {
  const options = await getOptions();
  if (!options.enabled || !options.e621BridgeEnabled) return { ok: true, skipped: true };
  const next = await apiFetch('/extension/e621/next', {});
  if (!next || !next.has_task || !next.task) return { ok: true, idle: true };
  const task = next.task;
  let managedWorkerTabId = 0;
  try {
    let result;
    try {
      let host = 'e621.net';
      try {
        const parsed = new URL(String(task.url || ''));
        if (parsed.hostname === 'e926.net') host = 'e926.net';
      } catch (_) {}
      const opened = await browserWorkerOpenUrl(`https://${host}/`);
      managedWorkerTabId = Number(opened.tabId || 0);
      result = await e621FetchInPage(managedWorkerTabId, task);
      result.bridge_mode = 'e621_shared_worker_page_fetch';
    } catch (pageErr) {
      if (managedWorkerTabId) await browserWorkerCloseManagedTab(managedWorkerTabId);
      managedWorkerTabId = 0;
      result = await e621FetchFromExtensionWorker(task);
      result.page_fetch_error = 'shared_worker_error=' + String(pageErr && pageErr.message || pageErr);
    }
    await apiFetchResultWithRetry('/extension/e621/result', {
      id: task.id,
      task_token: task.task_token || '',
      status: result.status,
      url: result.url,
      headers: result.headers || {},
      text: result.text || '',
      bridge_mode: result.bridge_mode || '',
      page_url: result.page_url || '',
      page_title: result.page_title || '',
      page_probe: result.page_probe || '',
      page_fetch_error: result.page_fetch_error || '',
    });
    if (managedWorkerTabId) {
      if ([401, 403, 429].includes(Number(result.status || 0)) || Number(result.status || 0) >= 500) {
        await browserWorkerCloseManagedTab(managedWorkerTabId);
      } else {
        await browserWorkerRetireAfterSuccess(managedWorkerTabId);
      }
    }
    return { ok: true, status: result.status, bridge_mode: result.bridge_mode || '' };
  } catch (err) {
    if (managedWorkerTabId) await browserWorkerCloseManagedTab(managedWorkerTabId);
    await apiFetchResultWithRetry('/extension/e621/result', {
      id: task.id,
      task_token: task.task_token || '',
      status: 0,
      url: task.url,
      headers: {},
      text: '',
      error: String(err && err.message || err),
      bridge_mode: 'failed',
    }).catch(() => {});
    return { ok: false, error: String(err && err.message || err) };
  }
}


function externalBrowserIdentity() {
  const ua = String((globalThis.navigator && navigator.userAgent) || '');
  const brands = Array.isArray(globalThis.navigator && navigator.userAgentData && navigator.userAgentData.brands)
    ? navigator.userAgentData.brands.map(x => String(x && x.brand || '')).join(' ')
    : '';
  let browser = 'Chromium-family';
  if (/Edg\//i.test(ua) || /Microsoft Edge/i.test(brands)) browser = 'Edge';
  else if (/OPR\//i.test(ua) || /Opera/i.test(brands)) browser = 'Opera';
  else if ((globalThis.navigator && navigator.brave) || /Brave/i.test(brands)) browser = 'Brave';
  else if (/Firefox\//i.test(ua)) browser = 'Firefox';
  else if (/Chrome\//i.test(ua) || /Chromium|Google Chrome/i.test(brands)) browser = 'Chrome/Chromium';
  return { browser, version: chrome.runtime.getManifest().version, user_agent: ua };
}

function externalBrowserChallengeState(snapshot) {
  const url = String(snapshot && snapshot.url || '').toLowerCase();
  const title = String(snapshot && snapshot.title || '').toLowerCase();
  const probe = String(snapshot && snapshot.probe || '').toLowerCase();
  const html = String(snapshot && snapshot.html || '').toLowerCase();
  const all = `${url}\n${title}\n${probe}\n${html.slice(0, 150000)}`;
  if (/too many requests|rate limit exceeded|error 429|http 429/.test(all)) {
    return { challenge: false, terminal: true, error: 'http_429' };
  }
  const challenge = /just a moment|checking your browser|verify you are human|attention required|cf-chl|challenge-platform|turnstile|enable javascript and cookies|cloudflare.*challenge|please (?:log|sign) in|login required|sign in to continue|you must be logged in|age verification|confirm your age|verify your age|are you (?:18|over 18)|adult content warning/.test(all);
  if (challenge) return { challenge: true, terminal: false, error: '' };
  if (/service unavailable|error 503|bad gateway|gateway timeout/.test(all)) {
    return { challenge: false, terminal: true, error: 'http_5xx' };
  }
  if (/access denied|you have been blocked|403 forbidden|error 403|request forbidden/.test(all)) {
    // rule34/hl.rule34 often expose Cloudflare or hotlink verification as a
    // generic 403 page. Keep the managed tab open and activate it so the user
    // can pass the wall instead of immediately closing and requeueing forever.
    let rule34Protected = false;
    try { rule34Protected = /(^|\.)rule34\.xxx$/i.test(new URL(String(snapshot && snapshot.url || '')).hostname); } catch (_) {}
    if (rule34Protected) return { challenge: true, terminal: false, error: '' };
    return { challenge: false, terminal: true, error: 'http_403_or_block' };
  }
  return { challenge: false, terminal: false, error: '' };
}

async function externalBrowserSnapshot(tabId) {
  const injected = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    func: () => ({
      url: String(location.href || ''),
      title: String(document.title || ''),
      probe: String(document.body && document.body.innerText || '').slice(0, 4000),
      html: String(document.documentElement && document.documentElement.outerHTML || '').slice(0, 3800000),
      ready: String(document.readyState || ''),
    }),
  });
  const first = Array.isArray(injected) && injected.length ? injected[0] : null;
  if (!first || !first.result) throw new Error('external_browser_snapshot_failed');
  return first.result;
}

function withBrowserBridgeMutex(fn) {
  const run = lbBrowserBridgeMutex.catch(() => {}).then(async () => {
    lbBrowserWorkerInUse += 1;
    try {
      return await fn();
    } finally {
      lbBrowserWorkerInUse = Math.max(0, lbBrowserWorkerInUse - 1);
    }
  });
  lbBrowserBridgeMutex = run.then(() => undefined, () => undefined);
  return run;
}

async function browserWorkerRememberTab(tabId) {
  const id = Number(tabId || 0);
  lbBrowserWorkerTabId = id;
  await STORAGE.set({
    [BROWSER_WORKER_TAB_KEY]: id,
    [LEGACY_EXTERNAL_WORKER_TAB_KEY]: 0,
    [BROWSER_WORKER_IDLE_STATE_KEY]: null,
  });
}

async function browserWorkerForgetTab(tabId = 0) {
  const expected = Number(tabId || 0);
  const stored = await STORAGE.get({
    [BROWSER_WORKER_TAB_KEY]: 0,
    [LEGACY_EXTERNAL_WORKER_TAB_KEY]: 0,
  });
  const current = Number(lbBrowserWorkerTabId || stored[BROWSER_WORKER_TAB_KEY] || stored[LEGACY_EXTERNAL_WORKER_TAB_KEY] || 0);
  if (expected && current && current !== expected) return false;
  lbBrowserWorkerTabId = 0;
  await STORAGE.set({
    [BROWSER_WORKER_TAB_KEY]: 0,
    [LEGACY_EXTERNAL_WORKER_TAB_KEY]: 0,
    [BROWSER_WORKER_IDLE_STATE_KEY]: null,
  });
  return true;
}

async function browserWorkerGetTab() {
  let id = Number(lbBrowserWorkerTabId || 0);
  if (!id) {
    const stored = await STORAGE.get({
      [BROWSER_WORKER_TAB_KEY]: 0,
      [LEGACY_EXTERNAL_WORKER_TAB_KEY]: 0,
    });
    id = Number(stored[BROWSER_WORKER_TAB_KEY] || stored[LEGACY_EXTERNAL_WORKER_TAB_KEY] || 0);
  }
  if (!id) return null;
  try {
    const tab = await chrome.tabs.get(id);
    if (tab && tab.id) {
      lbBrowserWorkerTabId = id;
      await STORAGE.set({ [BROWSER_WORKER_TAB_KEY]: id, [LEGACY_EXTERNAL_WORKER_TAB_KEY]: 0 });
      return tab;
    }
  } catch (_) {}
  await browserWorkerForgetTab(id);
  return null;
}

async function browserWorkerCancelIdleClose() {
  if (lbBrowserWorkerCloseTimer) clearTimeout(lbBrowserWorkerCloseTimer);
  lbBrowserWorkerCloseTimer = null;
  try { await chrome.alarms.clear(BROWSER_WORKER_IDLE_ALARM); } catch (_) {}
  await STORAGE.set({ [BROWSER_WORKER_IDLE_STATE_KEY]: null });
}

async function browserWorkerCloseManagedTab(tabId = 0) {
  const expected = Number(tabId || 0);
  const current = await browserWorkerGetTab();
  if (!current || !current.id) return;
  if (expected && Number(current.id) !== expected) return;
  await browserWorkerCancelIdleClose();
  try { await chrome.tabs.remove(current.id); } catch (_) {}
  await browserWorkerForgetTab(current.id);
}

async function browserWorkerHandleTabRemoved(tabId) {
  const id = Number(tabId || 0);
  if (!id) return;
  const current = await browserWorkerGetTab();
  if (current && Number(current.id) !== id) return;
  await browserWorkerCancelIdleClose();
  await browserWorkerForgetTab(id);
}

async function browserWorkerHandleIdleAlarm() {
  const state = await STORAGE.get({ [BROWSER_WORKER_IDLE_STATE_KEY]: null });
  const idle = state[BROWSER_WORKER_IDLE_STATE_KEY];
  if (!idle || !Number(idle.tab_id || 0)) return;
  const tabId = Number(idle.tab_id || 0);
  const deadline = Number(idle.deadline || 0);
  if (lbBrowserWorkerInUse > 0) {
    const retryAt = Date.now() + 5000;
    await STORAGE.set({ [BROWSER_WORKER_IDLE_STATE_KEY]: { tab_id: tabId, deadline: retryAt } });
    try { chrome.alarms.create(BROWSER_WORKER_IDLE_ALARM, { when: retryAt }); } catch (_) {}
    return;
  }
  if (deadline > Date.now() + 250) {
    try { chrome.alarms.create(BROWSER_WORKER_IDLE_ALARM, { when: deadline }); } catch (_) {}
    return;
  }
  await browserWorkerCloseManagedTab(tabId);
}

async function browserWorkerScheduleIdleClose(tabId) {
  const id = Number(tabId || 0);
  await browserWorkerCancelIdleClose();
  if (!id) return;
  const current = await browserWorkerGetTab();
  if (!current || Number(current.id) !== id) return;
  const deadline = Date.now() + BROWSER_WORKER_IDLE_MS;
  await STORAGE.set({ [BROWSER_WORKER_IDLE_STATE_KEY]: { tab_id: id, deadline } });
  try { chrome.alarms.create(BROWSER_WORKER_IDLE_ALARM, { when: deadline }); } catch (_) {}
  lbBrowserWorkerCloseTimer = setTimeout(() => { browserWorkerHandleIdleAlarm().catch(() => {}); }, BROWSER_WORKER_IDLE_MS + 100);
}

async function reconcileBrowserWorkerState() {
  const tab = await browserWorkerGetTab();
  if (!tab || !tab.id) return;
  const state = await STORAGE.get({ [BROWSER_WORKER_IDLE_STATE_KEY]: null });
  const idle = state[BROWSER_WORKER_IDLE_STATE_KEY];
  // A service-worker restart loses the in-memory lease.  A managed tab without
  // a durable idle deadline is therefore an interrupted/broken task, not a tab
  // that may safely be adopted as a user tab or left behind.
  if (!idle || Number(idle.tab_id || 0) !== Number(tab.id)) {
    await browserWorkerCloseManagedTab(tab.id);
    return;
  }
  if (Number(idle.deadline || 0) <= Date.now()) await browserWorkerCloseManagedTab(tab.id);
  else {
    try { chrome.alarms.create(BROWSER_WORKER_IDLE_ALARM, { when: Number(idle.deadline) }); } catch (_) {}
  }
}

async function browserWorkerClaimMarkedTab(tabId) {
  const id = Number(tabId || 0);
  if (!id) throw new Error('missing_marked_worker_tab_id');
  const previous = await browserWorkerGetTab();
  if (previous && previous.id && Number(previous.id) !== id) await browserWorkerCloseManagedTab(previous.id);
  await browserWorkerRememberTab(id);
  return id;
}

async function browserWorkerOpenUrl(url, { markedTabId = 0 } = {}) {
  await browserWorkerCancelIdleClose();
  const marked = Number(markedTabId || 0);
  if (marked) {
    try {
      await browserWorkerClaimMarkedTab(marked);
      await chrome.tabs.update(marked, { url, active: false });
      await waitForTabComplete(marked, 12000);
      return { tabId: marked, created: false, managed: true };
    } catch (err) {
      await browserWorkerCloseManagedTab(marked);
      throw err;
    }
  }
  const worker = await browserWorkerGetTab();
  if (worker && worker.id) {
    try {
      await chrome.tabs.update(worker.id, { url, active: false });
      await waitForTabComplete(worker.id, 12000);
      return { tabId: worker.id, created: false, managed: true };
    } catch (_) {
      await browserWorkerCloseManagedTab(worker.id);
    }
  }
  const tab = await chrome.tabs.create({ url, active: false });
  if (!tab || !tab.id) throw new Error('browser_worker_tab_create_failed');
  try {
    await browserWorkerRememberTab(tab.id);
  } catch (err) {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
    throw err;
  }
  return { tabId: tab.id, created: true, managed: true };
}

async function browserWorkerRetireAfterSuccess(tabId, { interactive = false } = {}) {
  const id = Number(tabId || 0);
  if (!id) return;
  if (interactive) {
    await browserWorkerCloseManagedTab(id);
    return;
  }
  try { await chrome.tabs.update(id, { url: 'about:blank', active: false }); } catch (_) {
    await browserWorkerCloseManagedTab(id);
    return;
  }
  await browserWorkerScheduleIdleClose(id);
}

async function externalBrowserFindOrCreateTab(task, senderTab) {
  let markedTabId = 0;
  if (senderTab && senderTab.id && senderTab.url) {
    try {
      const current = new URL(String(senderTab.url || ''));
      const marker = String(task.claim_marker || '');
      const marked = !!marker && current.hash.includes(`local-booru-resolver=${marker}`);
      if (marked) markedTabId = Number(senderTab.id);
    } catch (_) {}
  }
  return await browserWorkerOpenUrl(task.url, { markedTabId });
}

async function externalBrowserApplyUpload(tabId, task) {
  const injected = await chrome.scripting.executeScript({
    target: { tabId },
    world: 'MAIN',
    args: [{
      file_b64: String(task.file_b64 || ''),
      file_name: String(task.file_name || 'upload.bin'),
      file_mime: String(task.file_mime || 'application/octet-stream'),
      file_selectors: Array.isArray(task.file_selectors) ? task.file_selectors : ["input[type='file']"],
      submit_selectors: Array.isArray(task.submit_selectors) ? task.submit_selectors : [],
    }],
    func: async (payload) => {
      let input = null;
      for (const selector of payload.file_selectors) {
        try { input = document.querySelector(selector); } catch (_) {}
        if (input) break;
      }
      if (!input) return { upload_applied: false, submit_clicked: false, error: 'file_input_not_found' };
      const raw = atob(payload.file_b64 || '');
      const bytes = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
      const file = new File([bytes], payload.file_name, { type: payload.file_mime });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      let submit = null;
      for (const selector of payload.submit_selectors) {
        try { submit = document.querySelector(selector); } catch (_) {}
        if (submit) break;
      }
      let clicked = false;
      if (submit) {
        submit.click(); clicked = true;
      } else if (input.form) {
        if (typeof input.form.requestSubmit === 'function') input.form.requestSubmit();
        else input.form.submit();
        clicked = true;
      }
      return { upload_applied: true, submit_clicked: clicked, error: '' };
    },
  });
  const first = Array.isArray(injected) && injected.length ? injected[0] : null;
  return first && first.result ? first.result : { upload_applied: false, submit_clicked: false, error: 'upload_injection_failed' };
}

async function externalBrowserSubmitResult(task, payload) {
  const identity = externalBrowserIdentity();
  return await apiFetchResultWithRetry('/extension/browser/result', {
    id: task.id,
    token: task.token,
    task_token: task.task_token || '',
    status: Number(payload.status || 0),
    url: String(payload.url || task.url || ''),
    headers: payload.headers || {},
    text: String(payload.text || '').slice(0, 3800000),
    error: String(payload.error || ''),
    challenge: !!payload.challenge,
    bridge_mode: 'external_browser_extension',
    page_title: String(payload.page_title || ''),
    page_probe: String(payload.page_probe || '').slice(0, 2000),
    browser: identity.browser,
    upload_applied: !!payload.upload_applied,
    submit_clicked: !!payload.submit_clicked,
    ...identity,
  });
}

async function resolveExternalBrowserTask(task, senderTab) {
  const opened = await externalBrowserFindOrCreateTab(task, senderTab);
  const deadline = Date.now() + Math.max(5000, Number(task.timeout_seconds || 120) * 1000);
  let uploadApplied = false;
  let submitClicked = false;
  let uploadAt = 0;
  let last = { url: task.url, title: '', probe: '', html: '', ready: '' };
  let stableFingerprint = '';
  let stableCount = 0;
  let challengeActivated = false;
  let snapshotFailures = 0;
  try {
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      try {
        last = await externalBrowserSnapshot(opened.tabId);
        snapshotFailures = 0;
      } catch (_) {
        snapshotFailures += 1;
        if (snapshotFailures >= 3) throw new Error('worker_tab_unavailable');
        continue;
      }
      const state = externalBrowserChallengeState(last);
      if (state.terminal) {
        await externalBrowserSubmitResult(task, {
          status: 0, url: last.url, error: state.error, challenge: false,
          page_title: last.title, page_probe: last.probe,
          upload_applied: uploadApplied, submit_clicked: submitClicked,
        });
        await browserWorkerCloseManagedTab(opened.tabId);
        return { ok: false, error: state.error };
      }
      if (state.challenge) {
        if (!challengeActivated) {
          challengeActivated = true;
          try { await chrome.tabs.update(opened.tabId, { active: true }); } catch (_) {}
        }
        continue;
      }
      if (last.ready === 'loading') continue;
      if (task.action === 'upload_form' && !uploadApplied) {
        const applied = await externalBrowserApplyUpload(opened.tabId, task);
        uploadApplied = !!applied.upload_applied;
        submitClicked = !!applied.submit_clicked;
        if (!uploadApplied) {
          await externalBrowserSubmitResult(task, {
            status: 0, url: last.url, error: applied.error || 'upload_not_applied', challenge: false,
            page_title: last.title, page_probe: last.probe,
          });
          await browserWorkerCloseManagedTab(opened.tabId);
          return { ok: false, error: applied.error || 'upload_not_applied' };
        }
        uploadAt = Date.now();
        continue;
      }
      if (task.action === 'upload_form') {
        if (Date.now() - uploadAt < 3000) continue;
        const fingerprint = `${last.url}|${last.title}|${last.html.length}|${last.probe.slice(0, 240)}`;
        if (fingerprint === stableFingerprint) stableCount += 1;
        else { stableFingerprint = fingerprint; stableCount = 0; }
        if (stableCount < 2) continue;
      }
      await externalBrowserSubmitResult(task, {
        status: 200, url: last.url, text: last.html, challenge: false,
        page_title: last.title, page_probe: last.probe,
        upload_applied: uploadApplied, submit_clicked: submitClicked,
      });
      await browserWorkerRetireAfterSuccess(opened.tabId, { interactive: challengeActivated });
      return { ok: true, status: 200 };
    }
    const timeoutError = challengeActivated ? 'challenge_timeout' : 'worker_timeout';
    await externalBrowserSubmitResult(task, {
      status: 0, url: last.url, error: timeoutError, challenge: challengeActivated,
      page_title: last.title, page_probe: last.probe,
      upload_applied: uploadApplied, submit_clicked: submitClicked,
    });
    await browserWorkerCloseManagedTab(opened.tabId);
    return { ok: false, error: timeoutError };
  } catch (err) {
    const message = String(err && err.message || err);
    await externalBrowserSubmitResult(task, {
      status: 0, url: last.url || task.url, error: message, challenge: challengeActivated,
      page_title: last.title, page_probe: last.probe,
      upload_applied: uploadApplied, submit_clicked: submitClicked,
    }).catch(() => {});
    await browserWorkerCloseManagedTab(opened.tabId);
    return { ok: false, error: message };
  }
}

let lbExternalBrowserPollBusy = false;
async function pollExternalBrowserBridgeOnceUnlocked(senderTab) {
  if (lbExternalBrowserPollBusy) return { ok: true, busy: true };
  const options = await getOptions();
  if (!options.browserResolverEnabled) return { ok: true, disabled: true };
  lbExternalBrowserPollBusy = true;
  try {
    const identity = externalBrowserIdentity();
    const next = await apiFetch('/extension/browser/next', {
      ...identity,
      page_url: String(senderTab && senderTab.url || ''),
    });
    if (!next || !next.has_task || !next.task) return { ok: true, idle: true, busy: !!(next && next.busy) };
    return await resolveExternalBrowserTask(next.task, senderTab || null);
  } finally {
    lbExternalBrowserPollBusy = false;
  }
}


async function pollE621BridgeOnce(senderTab) {
  return await withBrowserBridgeMutex(() => pollE621BridgeOnceUnlocked(senderTab));
}

async function pollPixivBridgeOnce(senderTab) {
  return await withBrowserBridgeMutex(() => pollPixivBridgeOnceUnlocked(senderTab));
}

async function pollExternalBrowserBridgeOnce(senderTab) {
  return await withBrowserBridgeMutex(() => pollExternalBrowserBridgeOnceUnlocked(senderTab));
}


async function pumpBridgeTasks() {
  // Promise construction order is the mutex admission order. Protected-page
  // resolver work goes first so an R34 task cannot spend its whole queue budget
  // waiting behind Pixiv/e621 managed-tab fallbacks.
  await Promise.allSettled([
    pollExternalBrowserBridgeOnce(null),
    pollPixivBridgeOnce(null),
    pollE621BridgeOnce(null),
    pollMangaAsks(),                 // (0.10.0) «Сайты манги»: «Открыть сайт», «Обновить вход» (not a managed tab)
  ]);
}

async function startEventLoop() {
  if (eventLoopRunning) return;
  eventLoopRunning = true;
  try {
    let signature = '';
    while (true) {
      const options = await getOptions();
      if (!options.enabled || !options.pairingToken) break;
      try {
        const event = await apiFetch('/extension/events/wait', { signature });
        signature = String(event.signature || signature);
        flushOfflineQueue().catch(() => {});
        syncOfflineIndex().catch(() => {});
        const rev = String(event.status && event.status.blocklist_rev || '');
        const { blocklistRev } = await STORAGE.get({ blocklistRev: '' });
        syncBlocklist({ force: !!rev && rev !== blocklistRev }).catch(() => {});
        mangaSitesFollow(String(event.status && event.status.manga_signin || '')).catch(() => {});
        await pumpBridgeTasks();
      } catch (err) {
        await new Promise(resolve => setTimeout(resolve, 5000));
        if (String(err && err.message || err) === 'pairing_required') break;
      }
    }
  } finally {
    eventLoopRunning = false;
  }
}

// ── (0.10.0) the sign-in on the program's sites of manga ──────────────────────────────────────────────────────────
// The owner (6.11.2026): «авто сбор куки с сайтов которые нужны программе через расширение … открыл сайт куки
// собрались и с возможностью обновить их через расширение».  The program names its sites (/extension/manga/sites:
// their domains, the address to open, the localStorage key of a Lib site's token); a page of one of them loaded, its
// cookies changed, or «Обновить вход» of «Настройки → Сайты манги» — what this browser has of the site (its cookies,
// the token) goes to the program (/extension/manga/signin).  Only those sites, only with the permission for them
// (the manifest's; a new address — «Разрешить сайты манги» in the popup); nothing unchanged is sent twice.
const MANGA_SITES_EVERY_MS = 30 * 60 * 1000;
const MANGA_COOKIES_QUIET_MS = 8000;           // cookies of a site changed: sent when they are still this long
const mangaCookieTimers = {};
const mangaQuietTabs = new Set();               // the tabs «Обновить вход» opened for a moment: it sends for them

async function mangaSites({ force = false } = {}) {
  const kept = await STORAGE.get({ mangaSites: [], mangaSitesAt: 0 });
  if (!force && kept.mangaSites.length && Date.now() - kept.mangaSitesAt < MANGA_SITES_EVERY_MS) return kept.mangaSites;
  try {
    const r = await apiFetch('/extension/manga/sites', { version: chrome.runtime.getManifest().version }, { quiet: true });
    const sites = Array.isArray(r.sites) ? r.sites.filter(x => x && x.site && Array.isArray(x.domains)) : [];
    const { mangaSigninSent } = await STORAGE.get({ mangaSigninSent: {} });
    sites.filter(x => !x.kept).forEach(x => { delete mangaSigninSent[x.site]; });   // the program has none: again
    await STORAGE.set({ mangaSites: sites, mangaSitesAt: Date.now(), mangaSitesRev: String(r.rev || ''), mangaSigninSent });
    return sites;
  } catch (_) {
    return kept.mangaSites;                     // the program is closed: the list it gave last
  }
}

async function mangaSitesFollow(rev) {          // the long poll: a site's address changed in the program
  const sites = String(rev || '').split(':')[1] || '';
  const { mangaSitesRev } = await STORAGE.get({ mangaSitesRev: '' });
  if (sites && sites !== (String(mangaSitesRev).split(':')[1] || '')) await mangaSites({ force: true });
}

function mangaHostOf(url) {
  try {
    const u = new URL(String(url || ''));
    return /^https?:$/.test(u.protocol) ? u.hostname.toLowerCase() : '';
  } catch (_) {
    return '';
  }
}

function mangaSiteOf(sites, host) {
  host = String(host || '').toLowerCase().replace(/^\./, '');
  if (!host) return null;
  return sites.find(s => (s.domains || []).some(d => host === d || host.endsWith('.' + d))) || null;
}

function mangaRoots(site) {                     // its domains without the ones under another of them
  const all = (site.domains || []).map(d => String(d).toLowerCase());
  return all.filter(d => !all.some(o => o !== d && d.endsWith('.' + o)));
}

async function mangaTokenOf(tabId, key) {       // a Lib site's token: its own localStorage, read in its page
  if (!tabId || !key) return '';
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId }, args: [key],
      func: (name) => { try { return window.localStorage.getItem(name) || ''; } catch (_) { return ''; } },
    });
    return String(res && res.result || '').slice(0, 100000);
  } catch (_) {
    return '';
  }
}

async function mangaCollect(site, tabId = 0) {
  const seen = new Set(); const cookies = []; const missing = [];
  for (const domain of mangaRoots(site)) {
    let allowed = false;
    try { allowed = await chrome.permissions.contains({ origins: [`*://*.${domain}/*`] }); } catch (_) {}
    if (!allowed) { missing.push(domain); continue; }
    let list = [];
    try { list = await chrome.cookies.getAll({ domain }); } catch (_) {}
    for (const c of list) {
      const key = [c.domain, c.path, c.name].join('\t');
      if (seen.has(key)) continue;
      seen.add(key);
      cookies.push({ domain: c.domain, name: c.name, value: c.value, path: c.path,
        expirationDate: c.session ? 0 : Number(c.expirationDate || 0), session: !!c.session });
    }
  }
  // (0.10.1) this browser's User-Agent: a pass through a check for browsers (Cloudflare's cf_clearance — Nude-Moon) is
  // good only with the agent of the browser that got it
  return { cookies, token: site.token ? await mangaTokenOf(tabId, site.token) : '', missing,
    agent: String(navigator.userAgent || '').slice(0, 500) };
}

async function mangaDigest(got) {
  const text = JSON.stringify([got.cookies.map(c => [c.domain, c.path, c.name, c.value]).sort(), got.token || '',
    got.agent || '']);
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes)).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function mangaSend(site, got, asked = 0) {
  if (!asked && !got.cookies.length && !got.token) return { ok: true, empty: true };
  const digest = await mangaDigest(got);
  const { mangaSigninSent } = await STORAGE.get({ mangaSigninSent: {} });
  if (!asked && mangaSigninSent[site.site] === digest) return { ok: true, same: true };
  const r = await apiFetch('/extension/manga/signin', { site: site.site, cookies: got.cookies, token: got.token,
    missing: got.missing, asked, agent: got.agent || '', version: chrome.runtime.getManifest().version });
  mangaSigninSent[site.site] = digest;
  await STORAGE.set({ mangaSigninSent });
  return r;
}

async function mangaReady() {
  const options = await getOptions();
  return !!(options.enabled && options.pairingToken && options.mangaSignin !== false);
}

async function mangaTabLoaded(tabId, url) {
  if (mangaQuietTabs.has(tabId) || !(await mangaReady())) return;
  const site = mangaSiteOf(await mangaSites(), mangaHostOf(url));
  if (site) await mangaSend(site, await mangaCollect(site, tabId));
}

async function mangaOpenTabOf(site) {
  try {
    const tabs = await chrome.tabs.query({});
    const tab = tabs.find(t => mangaSiteOf([site], mangaHostOf(t.url)) && t.status === 'complete');
    return tab ? tab.id : 0;
  } catch (_) {
    return 0;
  }
}

async function mangaCookiesChanged(domain) {
  const site = mangaSiteOf(await mangaSites(), domain);
  if (!site || !(await mangaReady())) return;
  clearTimeout(mangaCookieTimers[site.site]);
  mangaCookieTimers[site.site] = setTimeout(async () => {
    delete mangaCookieTimers[site.site];
    try {
      if (await mangaReady()) await mangaSend(site, await mangaCollect(site, await mangaOpenTabOf(site)));
    } catch (_) {}
  }, MANGA_COOKIES_QUIET_MS);
}

async function mangaRefresh(site, asked) {      // «Обновить вход»: now; a Lib site's token — from its page
  let tabId = site.token ? await mangaOpenTabOf(site) : 0;
  let opened = 0;
  if (site.token && !tabId && site.open) {      // no page of it open: one in the background, for a moment
    try {
      const tab = await chrome.tabs.create({ url: site.open, active: false });
      opened = tab.id;
      mangaQuietTabs.add(opened);
      await waitForTabComplete(tab.id, 20000);
      tabId = tab.id;
    } catch (_) {}
  }
  try {
    return await mangaSend(site, await mangaCollect(site, tabId), asked);
  } finally {
    if (opened) {
      chrome.tabs.remove(opened).catch(() => {});
      setTimeout(() => mangaQuietTabs.delete(opened), 2000);
    }
  }
}

async function mangaOpen(site) {                // «Открыть сайт»: a tab of it in this browser, in front
  if (!site.open) return;
  const tab = await chrome.tabs.create({ url: site.open, active: true });
  try { if (tab && tab.windowId) await chrome.windows.update(tab.windowId, { focused: true }); } catch (_) {}
}

let mangaAsksBusy = false;
let mangaAsksAgain = false;                     // asked while busy: one more round when this one ends
async function pollMangaAsks() {
  if (mangaAsksBusy) { mangaAsksAgain = true; return; }
  const options = await getOptions();
  if (!options.pairingToken) return;
  mangaAsksBusy = true;
  try {
    do {
      mangaAsksAgain = false;
      const r = await apiFetch('/extension/manga/next', { version: chrome.runtime.getManifest().version }, { quiet: true });
      const asks = Array.isArray(r.asks) ? r.asks : [];
      if (!asks.length) continue;
      const sites = await mangaSites({ force: true });
      for (const ask of asks) {
        const site = sites.find(s => s.site === ask.site);
        if (!site) continue;
        try {
          if (ask.action === 'open') await mangaOpen(site);
          else if (ask.action === 'refresh') await mangaRefresh(site, Number(ask.id || 0) || 1);
        } catch (err) {
          await setFailure('manga_' + String(ask.action) + ':' + String(err && err.message || err));
        }
      }
    } while (mangaAsksAgain);
  } catch (_) {
    // the program is closed or older: nothing asked
  } finally {
    mangaAsksBusy = false;
  }
}

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info && info.status === 'complete' && tab && tab.url) mangaTabLoaded(tabId, tab.url).catch(() => {});
});
if (chrome.cookies && chrome.cookies.onChanged) {
  chrome.cookies.onChanged.addListener((change) => {
    const domain = change && change.cookie && change.cookie.domain;
    if (domain) mangaCookiesChanged(domain).catch(() => {});
  });
}

async function mangaPermissionOrigins() {       // the popup asks the browser for these (its button)
  const sites = await mangaSites({ force: true });
  return [...new Set(sites.flatMap(s => mangaRoots(s).map(d => `*://*.${d}/*`)))];
}

async function transferCurrentDomain(tab) {
  if (!tab || !tab.url) throw new Error('no_active_tab');
  const url = new URL(tab.url); const domain = url.hostname.replace(/^www\./, '');
  const cookies = await chrome.cookies.getAll({ domain });
  return await apiFetch('/extension/session/credentials', { domain, cookies, user_agent: navigator.userAgent || '' });
}

async function requestProfilePermissions(extraUrl = '') {
  const options = await getOptions();
  const profiles = options.profiles && options.profiles.length ? options.profiles : await refreshProfiles();
  const profileOrigins = profiles.flatMap(p => Array.isArray(p.origins) ? p.origins : []).filter(x => !x.includes('*://*/*') && x !== 'http://*/*' && x !== 'https://*/*');
  let arbitrary = Array.isArray(options.arbitraryOrigins) ? options.arbitraryOrigins.slice(0, 200) : [];
  try {
    const u = new URL(String(extraUrl || ''));
    if (u.protocol === 'http:' || u.protocol === 'https:') {
      const origin = `${u.protocol}//${u.hostname}/*`;
      if (!['127.0.0.1','localhost'].includes(u.hostname) && !arbitrary.includes(origin)) arbitrary.push(origin);
    }
  } catch (_) {}
  arbitrary = [...new Set(arbitrary)].slice(-200);
  const origins = [...new Set([...profileOrigins, ...arbitrary])];
  if (!origins.length) return { ok: true, granted: true, origins: [] };
  const granted = await chrome.permissions.request({ origins });
  if (granted) {
    await STORAGE.set({ arbitraryOrigins: arbitrary });
    const builtins = new Set(['rule34.xxx','gelbooru.com','danbooru.donmai.us','e621.net','e926.net','booru.allthefallen.moe','pixiv.net','tineye.com','ascii2d.net','generic']);
    const scripts = profiles.filter(p => !builtins.has(String(p.site_id || '').toLowerCase())).map(p => ({ id: 'lb-' + p.site_id.replace(/[^a-z0-9_-]/gi, '-'), matches: p.origins, js: ['content_script.js'], css: ['content_styles.css'], runAt: 'document_idle', persistAcrossSessions: true }));
    arbitrary.forEach((origin, index) => scripts.push({ id: 'lb-generic-' + index, matches: [origin], js: ['content_script.js'], css: ['content_styles.css'], runAt: 'document_idle', persistAcrossSessions: true }));
    try { const current = await chrome.scripting.getRegisteredContentScripts(); const ids = current.filter(x => String(x.id || '').startsWith('lb-')).map(x => x.id); if (ids.length) await chrome.scripting.unregisterContentScripts({ ids }); } catch (_) {}
    try { if (scripts.length) await chrome.scripting.registerContentScripts(scripts); } catch (err) { await setFailure('profile_registration:' + err.message); throw err; }
  }
  return { ok: true, granted, origins };
}

async function pixivImportWork(illustId) {
  const id = String(illustId || '').match(/\d+/)?.[0] || '';
  if (!id) throw new Error('missing_pixiv_illust_id');
  const headers = { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
  const [metaRes, pagesRes] = await Promise.all([
    fetch('https://www.pixiv.net/ajax/illust/' + id, { credentials: 'include', headers }),
    fetch('https://www.pixiv.net/ajax/illust/' + id + '/pages', { credentials: 'include', headers }),
  ]);
  const meta = await metaRes.json(); const pages = await pagesRes.json();
  if (meta.error || pages.error) throw new Error((meta.message || pages.message || 'pixiv_ajax_error'));
  const body = meta.body || {}; const pageRows = Array.isArray(pages.body) ? pages.body : [];
  const tags = (((body.tags || {}).tags) || []).map(x => String(x.tag || '')).filter(Boolean);
  const originalSource = String(body.urls && body.urls.original || body.original || '');
  const familyItems = pageRows.map((row, index) => ({ index, external_item_id: id + ':' + index, post_id: id, post_url: 'https://www.pixiv.net/artworks/' + id, media_url: String(row.urls && row.urls.original || ''), metadata: { width: row.width || 0, height: row.height || 0 } }));
  const family = { source: 'pixiv.net', type: 'pixiv_work', external_id: id, title: body.title || '', author: body.userName || '', total: familyItems.length || Number(body.pageCount || 1), items: familyItems, auto_complete: true };
  const items = familyItems.map(member => ({ site: 'pixiv.net', post_id: id, post_url: 'https://www.pixiv.net/artworks/' + id, file_url: member.media_url, media_urls: [member.media_url], tags, groups: { general: tags }, page_title: body.title || '', page_index: member.index, width: Number(member.metadata.width || 0), height: Number(member.metadata.height || 0), author_name: body.userName || '', author_id: String(body.userId || ''), caption: body.description || '', original_source: originalSource || member.media_url, family: { ...family, index: member.index } }));
  if (Number(body.illustType) === 2) {
    const ug = await fetch('https://www.pixiv.net/ajax/illust/' + id + '/ugoira_meta', { credentials: 'include', headers }).then(r => r.json());
    if (!ug.error && ug.body) {
      const zipUrl = String(ug.body.originalSrc || ug.body.src || '');
      items.length = 0;
      items.push({ site: 'pixiv.net', post_id: id, post_url: 'https://www.pixiv.net/artworks/' + id, file_url: zipUrl, media_urls: [zipUrl], tags, groups: { general: tags }, page_title: body.title || '', author_name: body.userName || '', author_id: String(body.userId || ''), caption: body.description || '', original_source: zipUrl, ugoira: { zip_url: zipUrl, frames: ug.body.frames || [] }, family: { ...family, type: 'pixiv_ugoira', total: 1, index: 0, items: [{ index: 0, media_url: zipUrl, post_url: 'https://www.pixiv.net/artworks/' + id }] } });
    }
  }
  return await importOrQueue('/extension/import_batch', { items }, items);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message !== 'object') return { ok: false, error: 'bad_message' };
    if (message.type === 'LB_GET_OPTIONS') {
      syncBlocklist().catch(() => {});             // at most once a minute; a new list comes to the tabs by itself
      return { ok: true, options: await getOptions() };
    }
    if (message.type === 'LB_SUB_STATE') return await subscriptionCall('/extension/subscription', message.url);
    if (message.type === 'LB_SUBSCRIBE') return await subscriptionCall('/extension/subscribe', message.url);
    if (message.type === 'LB_FAV_STATE') return await favoritesCall('/extension/favorites/state', message);
    if (message.type === 'LB_FAV_TAKE') return await favoritesCall('/extension/favorites', message);
    if (message.type === 'LB_CHECK_ITEMS') {
      const options = await getOptions();
      if (!options.enabled) return { ok: true, disabled: true, items: [] };
      const items = Array.isArray(message.items) ? message.items.slice(0, 250) : [];
      try {
        const result = await apiFetch('/extension/check', { items });
        syncOfflineIndex().catch(() => {});
        return result;
      } catch (err) {
        const sets = isOfflineError(err) ? await loadOfflineSets() : null;
        if (!sets) throw err;
        return { ok: true, offline: true, items: offlineCheckItems(sets, items) };
      }
    }
    if (message.type === 'LB_MANGA_CHECK') {      // (0.9.0) nhentai: what the manga folder has of these galleries
      const options = await getOptions();
      if (!options.enabled || options.mangaMode === 'off') return { ok: true, disabled: true, items: [] };
      const items = Array.isArray(message.items) ? message.items.slice(0, 300) : [];
      try {
        return await apiFetch('/extension/manga', { items });
      } catch (err) {
        if (isOfflineError(err)) return { ok: true, offline: true, items: [] };
        throw err;
      }
    }
    if (message.type === 'LB_OPEN_CARD') {        // «Уже есть» clicked: the card in the program's window
      const imageId = Number(message.imageId || 0);
      if (!(imageId > 0)) return { ok: false, error: 'no_card' };
      return await apiFetch('/extension/open', { image_id: imageId });
    }
    if (message.type === 'LB_IMPORT_ITEM') {
      const item = message.item || {};
      return await importOrQueue('/extension/import', { item }, [item]);
    }
    if (message.type === 'LB_IMPORT_ITEMS') {
      const items = Array.isArray(message.items) ? message.items : [];
      return await importOrQueue('/extension/import_batch', { items }, items);
    }
    if (message.type === 'LB_OFFLINE_STATUS') return await offlineStatus();
    if (message.type === 'LB_OFFLINE_SYNC') {
      const meta = await syncOfflineIndex({ force: true });
      const sent = await flushOfflineQueue();
      const blocklist = await syncBlocklist({ force: true });
      return { ok: true, index_count: Number(meta.count || 0), sent, blocklist_count: (blocklist || []).length };
    }
    if (message.type === 'LB_PAIR') {
      const result = await pairCompanion(String(message.code || ''), String(message.displayName || 'Local Booru Companion'));
      startEventLoop(); return result;
    }
    if (message.type === 'LB_UNPAIR') {
      let result = { ok: true, revoked: false };
      try { result = await apiFetch('/extension/pairing/revoke_self', {}); } finally { await STORAGE.set({ pairingToken: '', extensionId: '' }); }
      return result;
    }
    if (message.type === 'LB_REFRESH_PROFILES') return { ok: true, profiles: await refreshProfiles() };
    if (message.type === 'LB_REQUEST_PERMISSIONS') return await requestProfilePermissions(message.tab && message.tab.url || '');
    if (message.type === 'LB_TRANSFER_COOKIES') return await transferCurrentDomain(sender && sender.tab ? sender.tab : message.tab);
    if (message.type === 'LB_MANGA_ORIGINS') return { ok: true, origins: await mangaPermissionOrigins() };
    if (message.type === 'LB_MANGA_SEND_ALL') {   // (0.10.0) after «Разрешить сайты манги»: what the browser has now
      const sites = await mangaSites({ force: true });
      let sent = 0;
      for (const site of sites) {
        try { const r = await mangaSend(site, await mangaCollect(site, await mangaOpenTabOf(site))); if (r && r.changed) sent += 1; } catch (_) {}
      }
      return { ok: true, sites: sites.length, sent };
    }
    if (message.type === 'LB_PIXIV_IMPORT_WORK') return await pixivImportWork(message.illustId);
    if (message.type === 'LB_E621_POLL_ONCE') {
      return await pollE621BridgeOnce(sender && sender.tab ? sender.tab : null);
    }
    if (message.type === 'LB_PIXIV_POLL_ONCE') {
      return await pollPixivBridgeOnce(sender && sender.tab ? sender.tab : null);
    }
    if (message.type === 'LB_BROWSER_POLL_ONCE') {
      return await pollExternalBrowserBridgeOnce(sender && sender.tab ? sender.tab : null);
    }
    if (message.type === 'LB_STATUS') {
      const data = await apiFetch('/extension/status', {});
      startEventLoop();
      return { ok: data && data.ok !== false, status: data };
    }
    return { ok: false, error: 'unknown_message' };
  })().then(sendResponse).catch((err) => {
    sendResponse({ ok: false, error: String(err && err.message || err) });
  });
  return true;
});
