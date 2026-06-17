'use strict';

const API_BASE_DEFAULT = 'http://127.0.0.1:47734';
const DEFAULT_OPTIONS = {
  enabled: true,
  hideMode: 'hide', // hide | dim
  showBadge: true,
  apiBase: API_BASE_DEFAULT,
  e621BridgeEnabled: true,
};

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
});

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
    if (message.type === 'LB_STATUS') {
      const options = await getOptions();
      const base = String(options.apiBase || API_BASE_DEFAULT).replace(/\/$/, '');
      const res = await fetch(base + '/extension/status', { cache: 'no-store', credentials: 'omit' });
      const data = await res.json();
      return { ok: res.ok && data.ok !== false, status: data };
    }
    return { ok: false, error: 'unknown_message' };
  })().then(sendResponse).catch((err) => {
    sendResponse({ ok: false, error: String(err && err.message || err) });
  });
  return true;
});
