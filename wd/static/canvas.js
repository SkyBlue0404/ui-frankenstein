/* 画布模式（模式 B）—— 自由摆放的实时窗口面板 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);

const HANDLE_HIT = 10;
const MIN_W = 90;
const MIN_H = 60;

const app = {
  locked: false,
  filter: '',
  panels: new Map(), // id -> entry
  drag: null,        // 拖动 / 缩放状态
  modal: { win: null, region: null, dragging: false, start: null },
};

/* ================================================================== */
/* 启动                                                               */
/* ================================================================== */

async function boot() {
  bindToolbar();
  bindModal();
  await refresh();
  setInterval(pollStatus, 2000);
}

async function refresh() {
  const res = await fetch('/api/state', { cache: 'no-store' });
  const state = await res.json();
  $('#platform').textContent = state.platform || '—';
  syncPanels(state.tiles || []);
}

function syncPanels(tiles) {
  const canvas = $('#canvas');
  const seen = new Set();

  tiles.forEach((cfg, i) => {
    seen.add(cfg.id);
    const existing = app.panels.get(cfg.id);
    if (existing) {
      existing.cfg = cfg;
      applyStatus(existing, cfg);
    } else {
      const entry = createPanel(cfg, i);
      app.panels.set(cfg.id, entry);
      canvas.appendChild(entry.el);
    }
  });

  for (const [id, entry] of [...app.panels]) {
    if (!seen.has(id)) {
      clearTimeout(entry.retryTimer);
      entry.el.remove();
      app.panels.delete(id);
    }
  }

  $('#empty').hidden = tiles.length > 0;
  applyFilter();
}

/* ================================================================== */
/* 面板                                                               */
/* ================================================================== */

function autoRect(index) {
  const W = 430, H = 290, GAP = 18, PAD = 24, COLS = 3;
  return {
    x: PAD + (index % COLS) * (W + GAP),
    y: PAD + Math.floor(index / COLS) * (H + GAP),
    w: W, h: H,
  };
}

function createPanel(cfg, index) {
  const el = document.createElement('section');
  el.className = 'panel';
  el.dataset.id = cfg.id;
  el.innerHTML = `
    <header class="panel-head" data-role="head">
      <span class="dot" data-role="dot" title="未连接"></span>
      <span class="panel-title" data-role="title"></span>
      <button class="mini" data-role="pin" title="贴到桌面（Snipaste 风格）">📌</button>
      <button class="mini danger" data-role="del" title="从画布移除">✕</button>
    </header>
    <div class="panel-body" data-role="body">
      <img data-role="img" alt="" draggable="false">
      <div class="badge waiting" data-role="badge">连接中…</div>
    </div>
    <i class="handle" data-dir="nw"></i><i class="handle" data-dir="n"></i><i class="handle" data-dir="ne"></i>
    <i class="handle" data-dir="w"></i><i class="handle" data-dir="e"></i>
    <i class="handle" data-dir="sw"></i><i class="handle" data-dir="s"></i><i class="handle" data-dir="se"></i>`;

  const entry = {
    cfg,
    el,
    head: $('[data-role=head]', el),
    body: $('[data-role=body]', el),
    img: $('[data-role=img]', el),
    dot: $('[data-role=dot]', el),
    badge: $('[data-role=badge]', el),
    rect: cfg.canvas ? { ...cfg.canvas } : autoRect(index),
    retries: 0,
    retryTimer: null,
  };

  $('[data-role=title]', el).textContent = cfg.title || cfg.id;
  applyRect(entry, entry.rect);

  entry.streamUrl = () => `/stream/${encodeURIComponent(cfg.id)}?t=${Date.now()}`;
  entry.img.addEventListener('load', () => {
    entry.retries = 0;
    entry.badge.hidden = true;
  });
  entry.img.addEventListener('error', () => {
    if (!entry.el.isConnected) return;
    entry.retries += 1;
    if (entry.retries > 2) {
      entry.badge.hidden = false;
      entry.badge.className = 'badge';
      entry.badge.textContent = `画面流断开，重连中…（${entry.retries}）`;
    }
    clearTimeout(entry.retryTimer);
    entry.retryTimer = setTimeout(() => {
      if (entry.el.isConnected) entry.img.src = entry.streamUrl();
    }, Math.min(5000, 600 * entry.retries));
  });
  entry.img.src = entry.streamUrl();

  // 拖动（标题栏）
  entry.head.addEventListener('pointerdown', (evt) => startDrag(evt, entry, 'move'));
  // 缩放（手柄）
  el.querySelectorAll('.handle').forEach((h) => {
    h.addEventListener('pointerdown', (evt) => startDrag(evt, entry, 'resize', h.dataset.dir));
  });
  // 操作原窗口
  entry.body.addEventListener('click', (evt) => onBodyClick(evt, entry));
  entry.body.addEventListener('contextmenu', (evt) => evt.preventDefault());
  entry.body.addEventListener('wheel', (evt) => onBodyWheel(evt, entry), { passive: false });
  // 工具
  $('[data-role=pin]', el).addEventListener('click', async (e) => {
    e.stopPropagation();
    await pinTile(cfg.id);
  });
  $('[data-role=del]', el).addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`从画布移除「${cfg.title || cfg.id}」？`)) return;
    await fetch(`/api/tiles/${encodeURIComponent(cfg.id)}`, { method: 'DELETE' });
    await refresh();
  });

  applyStatus(entry, cfg);
  return entry;
}

function applyRect(entry, rect) {
  entry.el.style.left = `${Math.round(rect.x)}px`;
  entry.el.style.top = `${Math.round(rect.y)}px`;
  entry.el.style.width = `${Math.round(rect.w)}px`;
  entry.el.style.height = `${Math.round(rect.h)}px`;
}

function applyStatus(entry, cfg) {
  entry.cfg = cfg;
  entry.dot.classList.toggle('ok', !!cfg.ok);
  entry.dot.classList.toggle('err', !cfg.ok && !!cfg.error);
  entry.dot.title = cfg.ok ? '正常' : (cfg.error || '未连接');
  const locked = app.locked || cfg.interactive === false;
  entry.el.classList.toggle('locked', locked);

  if (cfg.error) {
    entry.badge.hidden = false;
    entry.badge.className = 'badge';
    entry.badge.textContent = cfg.error;
  } else if (cfg.ok) {
    entry.badge.hidden = true;
  }
}

async function pollStatus() {
  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    if (!res.ok) return;
    const state = await res.json();
    for (const cfg of state.tiles || []) {
      const entry = app.panels.get(cfg.id);
      if (entry) applyStatus(entry, cfg);
    }
  } catch { /* 下轮再试 */ }
}

/* ================================================================== */
/* 拖动 / 缩放                                                        */
/* ================================================================== */

function startDrag(evt, entry, mode, dir = '') {
  if (evt.button !== 0) return;
  evt.preventDefault();
  evt.stopPropagation();

  const canvasRect = $('#canvas').getBoundingClientRect();
  const base = { ...entry.rect };
  app.drag = {
    entry, mode, dir, base,
    startX: evt.clientX - canvasRect.left + $('#canvas').scrollLeft,
    startY: evt.clientY - canvasRect.top + $('#canvas').scrollTop,
  };
  entry.el.classList.add(mode === 'move' ? 'dragging' : 'resizing');
  try { entry.el.setPointerCapture(evt.pointerId); } catch { /* 合成事件 */ }

  const move = (e) => onDragMove(e);
  const up = (e) => {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    onDragEnd(e);
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', up);
}

function onDragMove(evt) {
  const d = app.drag;
  if (!d) return;
  const canvas = $('#canvas');
  const r = canvas.getBoundingClientRect();
  const x = evt.clientX - r.left + canvas.scrollLeft;
  const y = evt.clientY - r.top + canvas.scrollTop;
  const dx = x - d.startX;
  const dy = y - d.startY;

  let { x: bx, y: by, w, h } = d.base;
  if (d.mode === 'move') {
    bx = Math.max(0, bx + dx);
    by = Math.max(0, by + dy);
  } else {
    let x2 = bx + w;
    let y2 = by + h;
    if (d.dir.includes('w')) bx = Math.min(bx + dx, x2 - MIN_W);
    if (d.dir.includes('e')) x2 = Math.max(x2 + dx, bx + MIN_W);
    if (d.dir.includes('n')) by = Math.min(by + dy, y2 - MIN_H);
    if (d.dir.includes('s')) y2 = Math.max(y2 + dy, by + MIN_H);
    bx = Math.max(0, bx);
    by = Math.max(0, by);
    w = x2 - bx;
    h = y2 - by;
  }
  d.entry.rect = { x: bx, y: by, w, h };
  applyRect(d.entry, d.entry.rect);
}

async function onDragEnd() {
  const d = app.drag;
  app.drag = null;
  if (!d) return;
  d.entry.el.classList.remove('dragging', 'resizing');
  const { x, y, w, h } = d.entry.rect;
  try {
    await fetch(`/api/tiles/${encodeURIComponent(d.entry.cfg.id)}/canvas`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ x, y, w, h }),
    });
  } catch (err) {
    toast(`保存布局失败: ${err.message}`, true);
  }
}

/* ================================================================== */
/* 点击 / 滚轮回传                                                     */
/* ================================================================== */

function mapPoint(evt, img) {
  const rect = img.getBoundingClientRect();
  const nw = img.naturalWidth || 0;
  const nh = img.naturalHeight || 0;
  if (!nw || !nh || !rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / nw, rect.height / nh);
  const dw = nw * scale;
  const dh = nh * scale;
  const ox = (rect.width - dw) / 2;
  const oy = (rect.height - dh) / 2;
  const x = (evt.clientX - rect.left - ox) / scale;
  const y = (evt.clientY - rect.top - oy) / scale;
  if (x < 0 || y < 0 || x > nw || y > nh) return null;
  return { x, y };
}

function isInteractive(entry) {
  return !app.locked && entry.cfg.interactive !== false;
}

function onBodyClick(evt, entry) {
  if (!isInteractive(entry)) return;
  const p = mapPoint(evt, entry.img);
  if (!p) return;
  fetch(`/api/tiles/${encodeURIComponent(entry.cfg.id)}/input`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'click', x: p.x, y: p.y, button: evt.button + 1 }),
  }).catch(() => {});
}

let wheelPending = 0;
let wheelTimer = null;
function onBodyWheel(evt, entry) {
  if (!isInteractive(entry)) return;
  evt.preventDefault();
  const p = mapPoint(evt, entry.img);
  if (!p) return;
  wheelPending += evt.deltaY < 0 ? 1 : -1;
  if (wheelTimer) return;
  wheelTimer = setTimeout(() => {
    const delta = wheelPending;
    wheelPending = 0;
    wheelTimer = null;
    fetch(`/api/tiles/${encodeURIComponent(entry.cfg.id)}/input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'scroll', x: p.x, y: p.y, delta }),
    }).catch(() => {});
  }, 80);
}

/* ================================================================== */
/* 工具栏                                                             */
/* ================================================================== */

function bindToolbar() {
  const lockBtn = $('#btn-lock');
  lockBtn.addEventListener('click', () => {
    app.locked = !app.locked;
    lockBtn.textContent = app.locked ? '🔒 已锁定' : '🔓 可操作';
    lockBtn.classList.toggle('on', app.locked);
    for (const entry of app.panels.values()) applyStatus(entry, entry.cfg);
  });

  $('#btn-add').addEventListener('click', openModal);
  $('#btn-add-2').addEventListener('click', openModal);

  $('#btn-arrange').addEventListener('click', async () => {
    const list = [...app.panels.values()];
    for (let i = 0; i < list.length; i++) {
      const entry = list[i];
      entry.rect = autoRect(i);
      applyRect(entry, entry.rect);
      await fetch(`/api/tiles/${encodeURIComponent(entry.cfg.id)}/canvas`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry.rect),
      }).catch(() => {});
    }
    toast('已重新排列');
  });

  $('#filter').addEventListener('input', (e) => {
    app.filter = e.target.value.trim().toLowerCase();
    applyFilter();
  });
}

function applyFilter() {
  for (const entry of app.panels.values()) {
    const hay = `${entry.cfg.title || ''} ${entry.cfg.id}`.toLowerCase();
    entry.el.style.display = !app.filter || hay.includes(app.filter) ? '' : 'none';
  }
}

async function pinTile(tileId) {
  try {
    const res = await fetch(`/api/tiles/${encodeURIComponent(tileId)}/pin`, { method: 'POST' });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    toast('已贴到桌面');
  } catch (err) {
    toast(`贴图失败: ${err.message}`, true);
  }
}

/* ================================================================== */
/* 添加窗口弹窗                                                       */
/* ================================================================== */

const modal = {
  windows: [],
  selected: null,
  region: { x: 0, y: 0, w: 1, h: 1 },
};

function bindModal() {
  document.querySelectorAll('[data-close]').forEach((b) => {
    b.addEventListener('click', closeModal);
  });
  $('#modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') closeModal();
  });

  $('#win-filter').addEventListener('input', renderWindows);
  $('#btn-create').addEventListener('click', createFromModal);

  bindPickDrag();
}

async function openModal() {
  $('#modal').hidden = false;
  $('#pick-shot').hidden = true;
  $('#pick-title').textContent = '选一个窗口';
  $('#pick-hint').textContent = '在截图上拖拽框出要显示的区域';
  $('#btn-create').disabled = true;
  modal.selected = null;
  modal.region = { x: 0, y: 0, w: 1, h: 1 };
  const stage = $('#pick-stage');
  if (!$('.pick-empty', stage)) {
    stage.insertAdjacentHTML('afterbegin', '<div class="pick-empty">左侧点选一个窗口，这里显示它的截图</div>');
  }
  await loadWindows();
}

function closeModal() {
  $('#modal').hidden = true;
}

async function loadWindows() {
  try {
    const res = await fetch('/api/windows', { cache: 'no-store' });
    modal.windows = await res.json();
    renderWindows();
  } catch (err) {
    toast(`枚举窗口失败: ${err.message}`, true);
  }
}

function renderWindows() {
  const q = $('#win-filter').value.trim().toLowerCase();
  const list = $('#win-list');
  list.innerHTML = '';
  const items = modal.windows.filter((w) => {
    if (!q) return true;
    return w.title.toLowerCase().includes(q) || (w.app || '').toLowerCase().includes(q);
  });
  if (!items.length) {
    list.innerHTML = '<li class="pick-empty">没有匹配的窗口</li>';
    return;
  }
  for (const w of items) {
    const li = document.createElement('li');
    li.textContent = w.title;
    li.title = `${w.title}\n${w.app || ''}  ${w.rect.w}×${w.rect.h}`;
    li.classList.toggle('active', modal.selected && modal.selected.handle === w.handle);
    li.addEventListener('click', () => selectWindow(w));
    list.appendChild(li);
  }
}

async function selectWindow(w) {
  modal.selected = w;
  modal.region = { x: 0, y: 0, w: 1, h: 1 };
  renderWindows();
  $('#pick-title').textContent = w.title;

  const img = $('#pick-img');
  const shot = $('#pick-shot');
  const empty = $('.pick-empty', $('#pick-stage'));
  if (empty) empty.remove();

  try {
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error('截图加载失败'));
      img.src = `/api/snapshot?handle=${encodeURIComponent(w.handle)}&max_width=2400&t=${Date.now()}`;
    });
  } catch (err) {
    shot.hidden = true;
    toast(`抓取截图失败: ${err.message}`, true);
    return;
  }
  shot.hidden = false;
  $('#pick-sel').hidden = false;
  drawPickSel();
  $('#btn-create').disabled = false;
}

function bindPickDrag() {
  const shot = $('#pick-shot');
  const toNorm = (evt) => {
    const r = $('#pick-img').getBoundingClientRect();
    return {
      x: clamp01((evt.clientX - r.left) / r.width),
      y: clamp01((evt.clientY - r.top) / r.height),
    };
  };
  let start = null;

  shot.addEventListener('pointerdown', (evt) => {
    if (evt.target.closest('.pick-sel')) return;
    evt.preventDefault();
    start = toNorm(evt);
    modal.region = { x: start.x, y: start.y, w: 0, h: 0 };
    drawPickSel();
  });
  shot.addEventListener('pointermove', (evt) => {
    if (!start) return;
    const p = toNorm(evt);
    modal.region = {
      x: Math.min(start.x, p.x),
      y: Math.min(start.y, p.y),
      w: Math.abs(p.x - start.x),
      h: Math.abs(p.y - start.y),
    };
    drawPickSel();
  });
  shot.addEventListener('pointerup', () => {
    if (!start) return;
    start = null;
    if (modal.region.w < 0.004 || modal.region.h < 0.004) {
      modal.region = { x: 0, y: 0, w: 1, h: 1 };
    }
    drawPickSel();
  });
}

function drawPickSel() {
  const sel = $('#pick-sel');
  const img = $('#pick-img');
  if (!sel || sel.hidden) return;
  const W = img.clientWidth;
  const H = img.clientHeight;
  const { x, y, w, h } = modal.region;
  sel.style.left = `${x * W}px`;
  sel.style.top = `${y * H}px`;
  sel.style.width = `${Math.max(1, w * W)}px`;
  sel.style.height = `${Math.max(1, h * H)}px`;
  const nw = img.naturalWidth || 0;
  const nh = img.naturalHeight || 0;
  $('#pick-hint').textContent = nw
    ? `区域 ${Math.round(w * nw)}×${Math.round(h * nh)} px`
    : '在截图上拖拽框出要显示的区域';
}

async function createFromModal() {
  const w = modal.selected;
  if (!w) return;

  const title = prompt('面板标题', w.title.slice(0, 30)) ?? w.title.slice(0, 30);
  const id = `panel-${Date.now().toString(36)}`;
  const regex = `^${escapeRe(w.title)}$`;

  // 放到画布右下角空位
  const canvas = $('#canvas');
  const rect = {
    x: Math.max(20, canvas.scrollLeft + 40 + Math.random() * 120),
    y: Math.max(20, canvas.scrollTop + 40 + Math.random() * 120),
    w: 460,
    h: 300,
  };

  try {
    const res = await fetch(`/api/tiles/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id,
        title: title || id,
        window: { handle: '', title_regex: regex, app_regex: '', index: 0 },
        region: modal.region,
        canvas: rect,
        fps: 8,
        quality: 80,
        capture: 'window',
        interactive: true,
      }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    closeModal();
    await refresh();
    toast('面板已创建');
  } catch (err) {
    toast(`创建失败: ${err.message}`, true);
  }
}

/* ================================================================== */
/* 工具                                                               */
/* ================================================================== */

function clamp01(v) { return Math.max(0, Math.min(1, v)); }
function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = msg;
  el.className = isErr ? 'toast err' : 'toast';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

window.addEventListener('resize', () => {
  for (const entry of app.panels.values()) applyRect(entry, entry.rect);
});

boot();
