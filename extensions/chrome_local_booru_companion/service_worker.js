'use strict';

const API_BASE_DEFAULT = 'http://127.0.0.1:47734';
const DEFAULT_OPTIONS = {
  enabled: true,
  hideMode: 'hide', // hide | dim
  showBadge: true,
  apiBase: API_BASE_DEFAULT,
  e621BridgeEnabled: true,
  pixivBridgeEnabled: true,
};

const PIXIV_ALARM_NAME = 'lb-pixiv-bridge-poll';
const PIXIV_ALARM_MINUTES = 0.5; // Chrome MV3 practical minimum; content scripts still poll every 1.5s when available.
let lbPixivIntervalStarted = false;

function ensurePixivAlarm() {
  try {
    chrome.alarms.create(PIXIV_ALARM_NAME, { periodInMinutes: PIXIV_ALARM_MINUTES });
  } catch (_) {}
}

function startFastPixivPollWhileWorkerAlive() {
  if (lbPixivIntervalStarted) return;
  lbPixivIntervalStarted = true;
  // This does not keep Chrome alive forever; it only polls while the MV3 worker is already awake.
  // It covers the common case where a content script/popup woke the worker right before Local Booru enqueued a Pixiv task.
  setInterval(() => {
    pollPixivBridgeOnce(null).catch(() => {});
  }, 1500);
}

function isPixivUrl(u) {
  try {
    const h = new URL(String(u || '')).hostname;
    return h === 'pixiv.net' || h === 'www.pixiv.net' || h.endsWith('.pixiv.net');
  } catch (_) {
    return false;
  }
}


async function getOptions() {
  const data = await chrome.storage.sync.get(DEFAULT_OPTIONS);
  return { ...DEFAULT_OPTIONS, ...data };
}

async function apiFetch(path, payload) {
  const options = await getOptions();
  const base = String(options.apiBase || API_BASE_DEFAULT).replace(/\/$/, '');
  const res = await fetch(base + path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Local-Booru-Companion': '1',
    },
    body: JSON.stringify(payload || {}),
    credentials: 'omit',
    cache: 'no-store',
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (_) { data = { ok: false, error: 'bad_json', raw: text.slice(0, 200) }; }
  if (!res.ok || data.ok === false) {
    const err = new Error(data.error || ('HTTP ' + res.status));
    err.payload = data;
    throw err;
  }
  return data;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'lb-hide-card',
    title: 'Скрыть в Local Booru Companion',
    contexts: ['all'],
    documentUrlPatterns: [
      '*://rule34.xxx/*', '*://*.rule34.xxx/*', '*://gelbooru.com/*', '*://*.gelbooru.com/*',
      '*://danbooru.donmai.us/*', '*://e621.net/*', '*://e926.net/*', '*://booru.allthefallen.moe/*'
    ],
  });
  ensurePixivAlarm();
  startFastPixivPollWhileWorkerAlive();
});

chrome.runtime.onStartup.addListener(() => {
  ensurePixivAlarm();
  startFastPixivPollWhileWorkerAlive();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (!alarm || alarm.name !== PIXIV_ALARM_NAME) return;
  pollPixivBridgeOnce(null).catch(() => {});
});

ensurePixivAlarm();
startFastPixivPollWhileWorkerAlive();

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== 'lb-hide-card' || !tab || !tab.id) return;
  chrome.tabs.sendMessage(tab.id, { type: 'LB_CONTEXT_HIDE_AT_POINT' }).catch(() => {});
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

async function findE621Tab() {
  try {
    const tabs = await chrome.tabs.query({ url: ['*://e621.net/*', '*://e926.net/*'] });
    if (tabs && tabs.length) return tabs.find(t => t.active) || tabs[0];
  } catch (_) {}
  return null;
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

async function findPixivTab() {
  try {
    const tabs = await chrome.tabs.query({ url: ['*://www.pixiv.net/*', '*://pixiv.net/*'] });
    if (tabs && tabs.length) return tabs.find(t => t.active) || tabs[0];
  } catch (_) {}
  return null;
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

async function findPixivArtworkTab(artworkId) {
  if (!artworkId) return null;
  try {
    const patterns = [
      `*://www.pixiv.net/artworks/${artworkId}*`,
      `*://www.pixiv.net/en/artworks/${artworkId}*`,
      `*://pixiv.net/artworks/${artworkId}*`,
      `*://pixiv.net/en/artworks/${artworkId}*`,
    ];
    const tabs = await chrome.tabs.query({ url: patterns });
    if (tabs && tabs.length) return tabs.find(t => t.active) || tabs[0];
  } catch (_) {}
  return null;
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
  let tab = await findPixivArtworkTab(artworkId);
  let created = false;
  if (!tab || !tab.id) {
    tab = await chrome.tabs.create({ url: targetUrl, active: false });
    created = true;
  }
  try {
    await waitForTabComplete(tab.id, 11000);
    // Give Pixiv SPA a moment to hydrate meta/preload/user state.  Some Pixiv
    // pages report document complete before meta-preload-data is present.
    await new Promise(r => setTimeout(r, 1400));

    const isArtworkHtmlRequest = /\/artworks\/\d{5,}/.test(String((task && task.url) || ''));
    let result = null;

    if (isArtworkHtmlRequest) {
      result = await pixivDocumentHtmlFromPage(tab.id);
      result.bridge_mode = created ? 'pixiv_target_temp_tab_document_html' : 'pixiv_target_existing_tab_document_html';
    } else {
      result = await pixivFetchInPage(tab.id, task);
      result.bridge_mode = created ? 'pixiv_target_temp_tab_main_world_fetch' : 'pixiv_target_existing_tab_main_world_fetch';
      // If Pixiv still returns a masked AJAX 404/403/401 inside the exact artwork
      // page, return the target document HTML instead.  Local Booru's HTML pass
      // can parse meta-preload-data/pximg URLs from it, and this makes the log show
      // that the target-tab fallback really executed instead of hiding behind the
      // worker 404.
      if (result && (result.status === 404 || result.status === 403 || result.status === 401)) {
        try {
          const htmlResult = await pixivDocumentHtmlFromPage(tab.id);
          htmlResult.bridge_mode = created ? 'pixiv_target_temp_tab_document_html_after_ajax_' + result.status : 'pixiv_target_existing_tab_document_html_after_ajax_' + result.status;
          htmlResult.page_fetch_error = 'target_artwork_tab_html_after_ajax_status=' + result.status + '; target=' + targetUrl + '; created=' + (created ? 'yes' : 'no') + '; ajax_mode=' + (result.bridge_mode || '');
          result = htmlResult;
        } catch (htmlErr) {
          result.page_fetch_error = 'target_artwork_tab_ajax_status=' + result.status + '; target_html_error=' + String(htmlErr && htmlErr.message || htmlErr).slice(0, 120) + '; target=' + targetUrl + '; created=' + (created ? 'yes' : 'no');
        }
      }
    }

    result.page_fetch_error = (result.page_fetch_error || 'target_artwork_tab:' + targetUrl + '; created=' + (created ? 'yes' : 'no'));
    return result;
  } finally {
    if (created && tab && tab.id) {
      setTimeout(() => { chrome.tabs.remove(tab.id).catch(() => {}); }, 1500);
    }
  }
}

async function getPixivUserId(tab) {
  // Pixiv AJAX can return a fake 404 for logged-in/R18/private metadata when
  // x-user-id is missing.  Prefer the normal Pixiv cookie because it works from
  // the MV3 service worker without reading page JS state.
  try {
    if (chrome.cookies && chrome.cookies.get) {
      const c = await chrome.cookies.get({ url: 'https://www.pixiv.net/', name: 'PHPSESSID' });
      const raw = String((c && c.value) || '');
      const m = raw.match(/^(\d+)_/);
      if (m && m[1]) return { id: m[1], source: 'PHPSESSID' };
    }
  } catch (_) {}
  // Fallback: ask an already-open Pixiv tab only for pixiv.user.id.  We still do
  // the network fetch in the extension worker, so stale Pixiv pages cannot hide
  // the worker response with MAIN-world 404.
  try {
    const target = tab && tab.id ? tab : await findPixivTab();
    if (target && target.id) {
      const injected = await chrome.scripting.executeScript({
        target: { tabId: target.id },
        world: 'MAIN',
        func: () => {
          try {
            const p = window.pixiv || {};
            const u = p.user || {};
            return String(u.id || u.userId || '');
          } catch (_) { return ''; }
        },
      });
      const first = Array.isArray(injected) && injected.length ? injected[0] : null;
      const id = String((first && first.result) || '').trim();
      if (/^\d+$/.test(id)) return { id, source: 'pixiv.user.id' };
    }
  } catch (_) {}
  return { id: '', source: '' };
}

async function pixivFetchFromExtensionWorker(task, tabForUserId) {
  const user = await getPixivUserId(tabForUserId);
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

async function pollPixivBridgeOnce(senderTab) {
  const options = await getOptions();
  if (!options.enabled || !options.pixivBridgeEnabled) return { ok: true, skipped: true };
  const next = await apiFetch('/extension/pixiv/next', {});
  if (!next || !next.has_task || !next.task) return { ok: true, idle: true };
  const task = next.task;
  try {
    // v353: Pixiv source-MD5 donor must not depend on whichever Pixiv tab happens
    // to be open.  In real runs a stale/deleted Pixiv tab can make MAIN-world fetch
    // return 404 and hide the extension-worker result.  Use the MV3 service-worker
    // fetch first; it uses the normal browser cookie jar via host_permissions and
    // still never launches chrome.exe.  Keep page metadata only for diagnostics.
    let tab = (senderTab && senderTab.id && isPixivUrl(senderTab.url)) ? senderTab : null;
    if (!tab) tab = await findPixivTab();
    let result = await pixivFetchFromExtensionWorker(task, tab);
    result.bridge_mode = 'pixiv_extension_worker_fetch_forced';
    result.page_fetch_error = (tab && tab.url ? ('page_context_skipped:' + tab.url) : 'page_context_skipped:no_pixiv_tab') + '; user_id=' + (result.pixiv_user_id_present ? 'yes:' + (result.pixiv_user_id_source || 'unknown') : 'no');
    // v355: if the worker has cookies/user-id but Pixiv still masks metadata as 404,
    // retry inside a real tab opened on the target artwork itself.  This still uses
    // the already-open Chrome/Companion extension and never launches chrome.exe.
    // It avoids stale unrelated Pixiv tabs such as a previously opened test artwork.
    if (result && (result.status === 404 || result.status === 403 || result.status === 401)) {
      try {
        const workerStatus = result.status;
        const workerMode = result.bridge_mode || '';
        const workerNote = result.page_fetch_error || '';
        const tabResult = await pixivFetchFromTargetArtworkTab(task);
        if (tabResult) {
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
    await apiFetch('/extension/pixiv/result', {
      id: task.id,
      status: result.status,
      url: result.url,
      headers: result.headers || {},
      text: result.text || '',
      bridge_mode: result.bridge_mode || '',
      page_url: (tab && tab.url) || '',
      page_title: (tab && tab.title) || '',
      page_fetch_error: result.page_fetch_error || '',
    });
    return { ok: true, status: result.status, bridge_mode: result.bridge_mode || '' };
  } catch (err) {
    await apiFetch('/extension/pixiv/result', {
      id: task.id,
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

async function pollE621BridgeOnce(senderTab) {
  const options = await getOptions();
  if (!options.enabled || !options.e621BridgeEnabled) return { ok: true, skipped: true };
  const next = await apiFetch('/extension/e621/next', {});
  if (!next || !next.has_task || !next.task) return { ok: true, idle: true };
  const task = next.task;
  try {
    let tab = senderTab && senderTab.id ? senderTab : null;
    if (!tab) tab = await findE621Tab();
    let result;
    try {
      result = await e621FetchInPage(tab && tab.id, task);
    } catch (pageErr) {
      // Last-resort fallback keeps older extension installs functional, but Local
      // Booru can now tell from bridge_mode that the request did not run inside
      // the real e621 page context.
      result = await e621FetchFromExtensionWorker(task);
      result.page_fetch_error = String(pageErr && pageErr.message || pageErr);
    }
    await apiFetch('/extension/e621/result', {
      id: task.id,
      status: result.status,
      url: result.url,
      headers: result.headers || {},
      text: result.text || '',
      bridge_mode: result.bridge_mode || '',
      page_url: result.page_url || (tab && tab.url) || '',
      page_title: result.page_title || (tab && tab.title) || '',
      page_probe: result.page_probe || '',
      page_fetch_error: result.page_fetch_error || '',
    });
    return { ok: true, status: result.status, bridge_mode: result.bridge_mode || '' };
  } catch (err) {
    await apiFetch('/extension/e621/result', {
      id: task.id,
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

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message !== 'object') return { ok: false, error: 'bad_message' };
    if (message.type === 'LB_GET_OPTIONS') {
      return { ok: true, options: await getOptions() };
    }
    if (message.type === 'LB_CHECK_ITEMS') {
      const options = await getOptions();
      if (!options.enabled) return { ok: true, disabled: true, items: [] };
      const items = Array.isArray(message.items) ? message.items.slice(0, 250) : [];
      return await apiFetch('/extension/check', { items });
    }
    if (message.type === 'LB_HIDE_ITEM') {
      return await apiFetch('/extension/hide', { item: message.item || {}, query: message.query || '' });
    }
    if (message.type === 'LB_E621_POLL_ONCE') {
      return await pollE621BridgeOnce(sender && sender.tab ? sender.tab : null);
    }
    if (message.type === 'LB_PIXIV_POLL_ONCE') {
      return await pollPixivBridgeOnce(sender && sender.tab ? sender.tab : null);
    }
    if (message.type === 'LB_STATUS') {
      const options = await getOptions();
      const base = String(options.apiBase || API_BASE_DEFAULT).replace(/\/$/, '');
      const res = await fetch(base + '/extension/status', { headers: { 'X-Local-Booru-Companion': '1' }, cache: 'no-store', credentials: 'omit' });
      const data = await res.json();
      return { ok: res.ok && data.ok !== false, status: data };
    }
    return { ok: false, error: 'unknown_message' };
  })().then(sendResponse).catch((err) => {
    sendResponse({ ok: false, error: String(err && err.message || err) });
  });
  return true;
});
