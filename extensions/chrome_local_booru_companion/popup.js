'use strict';
const DEFAULTS = { enabled: true, hideMode: 'hide', showBadge: true, apiBase: 'http://127.0.0.1:47734' };
const $ = (id) => document.getElementById(id);

async function load() {
  const data = await chrome.storage.sync.get(DEFAULTS);
  $('enabled').checked = !!data.enabled;
  $('showBadge').checked = !!data.showBadge;
  $('hideMode').value = data.hideMode || 'hide';
  $('apiBase').value = data.apiBase || DEFAULTS.apiBase;
}

async function save() {
  await chrome.storage.sync.set({
    enabled: $('enabled').checked,
    showBadge: $('showBadge').checked,
    hideMode: $('hideMode').value,
    apiBase: $('apiBase').value.trim() || DEFAULTS.apiBase,
  });
  $('out').textContent = 'Сохранено. Обнови страницу booru.';
}

async function status() {
  $('out').textContent = 'Проверяю...';
  try {
    const r = await chrome.runtime.sendMessage({ type: 'LB_STATUS' });
    $('out').textContent = r && r.ok ? 'Связь есть: v' + (r.status && r.status.version || '?') : 'Нет связи';
  } catch (e) {
    $('out').textContent = 'Нет связи. Открой Local Booru.';
  }
}

$('save').addEventListener('click', save);
$('status').addEventListener('click', status);
load();
