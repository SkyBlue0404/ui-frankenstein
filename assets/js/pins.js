/* 贴图模式（模式 A）—— 全局快捷键截取 + 贴图管理 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);

const state = { pins: new Map(), hotkey: 'ctrl+alt+p' };

async function boot() {
  $('#btn-capture').addEventListener('click', captureFocus);
  $('#hotkey').addEventListener('click', recordHotkey);
  $('#btn-quit').addEventListener('click', async () => {
    if (!confirm('退出后台进程？已贴到桌面的窗口也会一起关掉。')) return;
    try {
      await fetch('/api/quit', { method: 'POST' });
    } catch { /* 进程已经没了 */ }
    toast('已退出后台');
  });
  await refresh();
  setInterval(refresh, 2500);
}

/* ---------------- 快捷键录制 ---------------- */

let recording = false;

function prettyHotkey(spec) {
  return String(spec)
    .split('+')
    .map((s) => s.trim())
    .map((s) => ({
      ctrl: 'Ctrl', control: 'Ctrl', alt: 'Alt', shift: 'Shift',
      super: 'Super', win: 'Win', meta: 'Super',
    }[s.toLowerCase()] || s.toUpperCase()))
    .join(' + ');
}

function normalizeKey(e) {
  // 优先用 e.code（物理键位）：这样 Shift+1 不会变成 "!"，
  // 也不会因为输入法/布局差异拿到奇怪字符
  const code = e.code || '';
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^Numpad[0-9]$/.test(code)) return code.slice(6);
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code.toLowerCase();

  const named = {
    Space: 'space', Tab: 'tab', Enter: 'enter', NumpadEnter: 'enter',
    Escape: 'escape', Backspace: 'backspace', Insert: 'insert', Delete: 'delete',
    Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown',
    ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right',
    Minus: 'minus', Equal: 'equal', Comma: 'comma', Period: 'period',
    Slash: 'slash', Backslash: 'backslash', Backquote: 'grave',
    BracketLeft: 'bracketleft', BracketRight: 'bracketright',
    Semicolon: 'semicolon', Quote: 'apostrophe',
  };
  if (named[code]) return named[code];

  // 没有 code 的兜底
  const k = e.key;
  if (!k) return '';
  if (k === ' ') return 'space';
  if (/^F\d{1,2}$/.test(k)) return k.toLowerCase();
  if (/^[A-Za-z0-9]$/.test(k)) return k.toLowerCase();
  return '';
}

function recordHotkey() {
  if (recording) return;
  recording = true;
  const btn = $('#hotkey');
  const oldText = btn.textContent;
  btn.classList.add('recording');
  btn.textContent = '按下快捷键…';

  const stop = () => {
    recording = false;
    btn.classList.remove('recording');
    document.removeEventListener('keydown', onKey, true);
  };

  const onKey = async (e) => {
    e.preventDefault();
    e.stopPropagation();

    if (e.key === 'Escape' && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {
      stop();
      btn.textContent = oldText;
      toast('已取消');
      return;
    }

    const key = normalizeKey(e);
    if (!key) return; // 只按了修饰键或不支持的键，继续等

    const mods = [];
    if (e.ctrlKey) mods.push('ctrl');
    if (e.altKey) mods.push('alt');
    if (e.shiftKey) mods.push('shift');
    if (e.metaKey) mods.push('super');

    // 单字母/数字会拦截正常输入，提醒一下但允许设置
    if (!mods.length && /^[a-z0-9]$/.test(key)) {
      if (!confirm(`「${key.toUpperCase()}」是普通按键，设为全局快捷键后会拦截所有输入。\n确定要用它吗？`)) {
        return;
      }
    }

    stop();
    const spec = [...mods, key].join('+');
    try {
      const res = await fetch('/api/hotkey', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hotkey: spec }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      state.hotkey = spec;
      btn.textContent = prettyHotkey(spec);
      toast(`快捷键已改为 ${prettyHotkey(spec)}`);
    } catch (err) {
      btn.textContent = oldText;
      toast(`设置失败: ${err.message}`, true);
    }
  };

  document.addEventListener('keydown', onKey, true);
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
