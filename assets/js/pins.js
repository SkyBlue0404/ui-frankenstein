/* 贴图模式（模式 A）—— 全局快捷键截取 + 贴图管理 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);

const state = { pins: new Map(), hotkey: 'ctrl+alt+p' };

async function boot() {
  $('#btn-capture').addEventListener('click', captureFocus);
  await refresh();
  setInterval(refresh, 2500);
}

function prettyHotkey(spec) {
  return String(spec)
    .split('+')
    .map((s) => s.trim())
    .map((s) => ({ ctrl: 'Ctrl', control: 'Ctrl', alt: 'Alt', shift: 'Shift', super: 'Super', win: 'Win' }[s.toLowerCase()] || s.toUpperCase()))
    .join(' + ');
}

async function refresh() {
  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    const data = await res.json();
    $('#platform').textContent = data.platform || '—';
    if (data.hotkey && data.hotkey !== state.hotkey) {
      state.hotkey = data.hotkey;
      $('#hotkey').textContent = prettyHotkey(data.hotkey);
    }
    // 瞬时贴图 tile 的 id 以 pin- 开头
    sync((data.tiles || []).filter((t) => t.id.startsWith('pin-')));
  } catch { /* 下轮再试 */ }
}

function sync(tiles) {
  const grid = $('#pin-grid');
  const seen = new Set();

  for (const cfg of tiles) {
    seen.add(cfg.id);
    let entry = state.pins.get(cfg.id);
    if (!entry) {
      entry = makePin(cfg);
      state.pins.set(cfg.id, entry);
      grid.appendChild(entry.el);
    }
    entry.cfg = cfg;
    entry.el.querySelector('.pin-meta').textContent =
      cfg.ok ? `${cfg.crop_size[0]}×${cfg.crop_size[1]} · ${cfg.fps.toFixed(0)}fps` : (cfg.error || '');
    entry.el.querySelector('.dot').className = `dot ${cfg.ok ? 'ok' : (cfg.error ? 'err' : '')}`;
  }

  for (const [id, entry] of [...state.pins]) {
    if (!seen.has(id)) {
      clearTimeout(entry.retryTimer);
      entry.el.remove();
      state.pins.delete(id);
    }
  }

  $('#pin-count').textContent = String(tiles.length);
  $('#pin-empty').hidden = tiles.length > 0;
}

function makePin(cfg) {
  const el = document.createElement('article');
  el.className = 'pin-card';
  el.innerHTML = `
    <header class="pin-card-head">
      <span class="dot"></span>
      <span class="pin-title"></span>
      <button class="mini danger" title="关闭贴图">✕</button>
    </header>
    <div class="pin-thumb"><img alt="" draggable="false"></div>
    <footer class="pin-card-foot">
      <span class="pin-meta"></span>
      <button class="btn small" data-act="pin">重新贴到桌面</button>
    </footer>`;

  const img = $('img', el);
  const entry = { cfg, el, retries: 0, retryTimer: null };

  $('.pin-title', el).textContent = cfg.title || cfg.id;

  const url = () => `/stream/${encodeURIComponent(cfg.id)}?t=${Date.now()}`;
  img.addEventListener('error', () => {
    if (!el.isConnected) return;
    entry.retries += 1;
    clearTimeout(entry.retryTimer);
    entry.retryTimer = setTimeout(() => {
      if (el.isConnected) img.src = url();
    }, Math.min(5000, 600 * entry.retries));
  });
  img.src = url();

  $('.mini', el).addEventListener('click', async () => {
    // 关掉这一张：从服务端移除瞬时 tile
    await fetch(`/api/tiles/${encodeURIComponent(cfg.id)}`, { method: 'DELETE' });
    await refresh();
  });

  $('[data-act=pin]', el).addEventListener('click', async () => {
    try {
      const res = await fetch(`/api/tiles/${encodeURIComponent(cfg.id)}/pin`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast('已重新贴到桌面');
    } catch (err) {
      toast(`贴图失败: ${err.message}`, true);
    }
  });

  return entry;
}

async function captureFocus() {
  try {
    const res = await fetch('/api/capture-focus', { method: 'POST' });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    toast('遮罩已弹出，拖拽框选区域');
  } catch (err) {
    toast(`截取失败: ${err.message}（先聚焦一个窗口再试）`, true);
  }
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = isErr ? 'toast err' : 'toast';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3000);
}

boot();
