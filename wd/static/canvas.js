/* 画布模式（模式 B）—— 自由摆放的实时窗口面板 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);

const MIN_W = 80;
const MIN_H = 40;
const DRAG_THRESHOLD = 4; // 超过这个位移算拖拽，否则算点击

const app = {
  locked: false,
  filter: '',
  panels: new Map(), // id -> entry
  press: null,       // 按下状态（拖拽 / 点击判定）
  resize: null,      // 缩放状态
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
  const W = 430, H = 260, GAP = 16, PAD = 20, COLS = 3;
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
    <div class="panel-body" data-role="body">
      <img data-role="img" alt="" draggable="false">
      <div class="badge waiting" data-role="badge">连接中…</div>
    </div>
    <span class="dot" data-role="dot" title="未连接"></span>
    <div class="panel-tools">
      <button class="mini" data-act="pin" title="贴到桌面（Snipaste 风格）">📌</button>
      <select class="scale" data-act="scale" title="缩放">
        <option value="0.5">50%</option>
        <option value="0.75">75%</option>
        <option value="1" selected>100%</option>
        <option value="1.5">150%</option>
        <option value="2">200%</option>
        <option value="3">300%</option>
      </select>
      <button class="mini danger" data-act="close" title="从画布移除">✕</button>
    </div>
    <i class="handle" data-dir="nw"></i><i class="handle" data-dir="n"></i><i class="handle" data-dir="ne"></i>
    <i class="handle" data-dir="w"></i><i class="handle" data-dir="e"></i>
    <i class="handle" data-dir="sw"></i><i class="handle" data-dir="s"></i><i class="handle" data-dir="se"></i>`;

  const entry = {
    cfg,
    el,
    body: $('[data-role=body]', el),
    img: $('[data-role=img]', el),
    dot: $('[data-role=dot]', el),
    badge: $('[data-role=badge]', el),
    rect: cfg.canvas ? { ...cfg.canvas } : autoRect(index),
    sized: !!cfg.canvas,
    retries: 0,
    retryTimer: null,
  };

  el.title = cfg.title || cfg.id;
  applyRect(entry, entry.rect);

  entry.streamUrl = () => `/stream/${encodeURIComponent(cfg.id)}?t=${Date.now()}`;
  entry.img.addEventListener('load', () => {
    entry.retries = 0;
    entry.badge.hidden = true;
    if (!entry.sized) {
      entry.sized = true;
      // 面板尺寸跟随实际裁剪画面（等比），超过 560px 宽就缩下来
      const nw = entry.img.naturalWidth || 0;
      const nh = entry.img.naturalHeight || 0;
      if (nw && nh) {
        const f = Math.min(1, 560 / nw);
        entry.rect = {
          ...entry.rect,
          w: Math.max(MIN_W, Math.round(nw * f)),
          h: Math.max(MIN_H, Math.round(nh * f)),
        };
        applyRect(entry, entry.rect);
      }
    }
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

  // 按下：拖拽 或 点击（按位移阈值区分）
  entry.body.addEventListener('pointerdown', (evt) => startPress(evt, entry));
  entry.body.addEventListener('contextmenu', (evt) => evt.preventDefault());
  entry.body.addEventListener('wheel', (evt) => onWheel(evt, entry), { passive: false });

  // 8 向缩放手柄
  el.querySelectorAll('.handle').forEach((h) => {
    h.addEventListener('pointerdown', (evt) => startResize(evt, entry, h.dataset.dir));
  });

  // 悬浮工具条
  $('[data-act=pin]', el).addEventListener('click', async (e) => {
    e.stopPropagation();
    await pinTile(cfg.id);
  });
  $('[data-act=close]', el).addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`从画布移除「${cfg.title || cfg.id}」？`)) return;
    await fetch(`/api/tiles/${encodeURIComponent(cfg.id)}`, { method: 'DELETE' });
    await refresh();
  });
  $('[data-act=scale]', el).addEventListener('change', (e) => {
    e.stopPropagation();
    applyScale(entry, parseFloat(e.target.value) || 1);
  });
  $('[data-act=scale]', el).addEventListener('pointerdown', (e) => e.stopPropagation());

  applyStatus(entry, cfg);
  return entry;
}

function applyRect(entry, rect) {
  entry.el.style.left = `${Math.round(rect.x)}px`;
  entry.el.style.top = `${Math.round(rect.y)}px`;
  entry.el.style.width = `${Math.round(rect.w)}px`;
  entry.el.style.height = `${Math.round(rect.h)}px`;
}

function applyScale(entry, factor) {
  const nw = entry.img.naturalWidth || 0;
  const nh = entry.img.naturalHeight || 0;
  if (!nw || !nh) return;
  entry.rect = {
    ...entry.rect,
    w: Math.max(MIN_W, Math.round(nw * factor)),
    h: Math.max(MIN_H, Math.round(nh * factor)),
  };
  entry.sized = true;
  applyRect(entry, entry.rect);
  saveRect(entry);
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
/* 拖拽 / 点击                                                        */
/* ================================================================== */

function canvasPoint(evt) {
  const canvas = $('#canvas');
  const r = canvas.getBoundingClientRect();
  return { x: evt.clientX - r.left + canvas.scrollLeft, y: evt.clientY - r.top + canvas.scrollTop };
}

function startPress(evt, entry) {
  if (evt.button !== 0) return;
  if (evt.target.closest('.panel-tools')) return;
  evt.preventDefault();

  const p = canvasPoint(evt);
  app.press = {
    entry,
    startX: p.x,
    startY: p.y,
    base: { ...entry.rect },
    moved: false,
    pointerId: evt.pointerId,
  };
  try { entry.body.setPointerCapture(evt.pointerId); } catch { /* 合成事件 */ }
  document.addEventListener('pointermove', onPressMove);
  document.addEventListener('pointerup', onPressUp);
}

function onPressMove(evt) {
  const p = app.press;
  if (!p) return;
  const cur = canvasPoint(evt);
  const dx = cur.x - p.startX;
  const dy = cur.y - p.startY;

  if (!p.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
    p.moved = true;
    p.entry.el.classList.add('dragging');
  }
  if (p.moved) {
    p.entry.rect = {
      ...p.base,
      x: Math.max(0, p.base.x + dx),
      y: Math.max(0, p.base.y + dy),
    };
    applyRect(p.entry, p.entry.rect);
  }
}

async function onPressUp(evt) {
  const p = app.press;
  app.press = null;
  document.removeEventListener('pointermove', onPressMove);
  document.removeEventListener('pointerup', onPressUp);
  if (!p) return;
  try { p.entry.body.releasePointerCapture(p.pointerId); } catch { /* ignore */ }

  if (p.moved) {
    p.entry.el.classList.remove('dragging');
    await saveRect(p.entry);
  } else {
    forwardClick(p.entry, evt);
  }
}

/* ================================================================== */
/* 缩放                                                               */
/* ================================================================== */

function startResize(evt, entry, dir) {
  if (evt.button !== 0) return;
  evt.preventDefault();
  evt.stopPropagation();

  const p = canvasPoint(evt);
  app.resize = {
    entry, dir,
    startX: p.x,
    startY: p.y,
    base: { ...entry.rect },
    pointerId: evt.pointerId,
  };
  entry.el.classList.add('resizing');
  entry.sized = true;
  try { entry.el.setPointerCapture(evt.pointerId); } catch { /* 合成事件 */ }
  document.addEventListener('pointermove', onResizeMove);
  document.addEventListener('pointerup', onResizeUp);
}

function onResizeMove(evt) {
  const s = app.resize;
  if (!s) return;
  const cur = canvasPoint(evt);
  const dx = cur.x - s.startX;
  const dy = cur.y - s.startY;

  let { x, y, w, h } = s.base;
  let x2 = x + w;
  let y2 = y + h;
  if (s.dir.includes('w')) x = Math.min(x + dx, x2 - MIN_W);
  if (s.dir.includes('e')) x2 = Math.max(x2 + dx, x + MIN_W);
  if (s.dir.includes('n')) y = Math.min(y + dy, y2 - MIN_H);
  if (s.dir.includes('s')) y2 = Math.max(y2 + dy, y + MIN_H);
  x = Math.max(0, x);
  y = Math.max(0, y);
  s.entry.rect = { x, y, w: x2 - x, h: y2 - y };
  applyRect(s.entry, s.entry.rect);
}

async function onResizeUp(evt) {
  const s = app.resize;
  app.resize = null;
  document.removeEventListener('pointermove', onResizeMove);
  document.removeEventListener('pointerup', onResizeUp);
  if (!s) return;
  try { s.entry.el.releasePointerCapture(s.pointerId); } catch { /* ignore */ }
  s.entry.el.classList.remove('resizing');
  await saveRect(s.entry);
}

async function saveRect(entry) {
  const { x, y, w, h } = entry.rect;
  try {
    await fetch(`/api/tiles/${encodeURIComponent(entry.cfg.id)}/canvas`, {
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

function forwardClick(entry, evt) {
  if (!isInteractive(entry)) return;
  const p = mapPoint(evt, entry.img);
  if (!p) return;
  fetch(`/api/tiles/${encodeURIComponent(entry.cfg.id)}/input`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'click', x: p.x, y: p.y, button: 1 }),
  }).catch(() => {});
}

let wheelPending = 0;
let wheelTimer = null;
function onWheel(evt, entry) {
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
      entry.rect = { ...autoRect(i), w: entry.rect.w, h: entry.rect.h };
      applyRect(entry, entry.rect);
      await saveRect(entry);
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
  document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', closeModal));
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
      x: Math.max(0, Math.min(1, (evt.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (evt.clientY - r.top) / r.height)),
    };
  };
  let start = null;

  shot.addEventListener('pointerdown', (evt) => {
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

  // 面板尺寸 = 选区实际像素（超过 560 宽就等比缩下来）
  const nw = Math.max(1, Math.round(w.rect.w * modal.region.w));
  const nh = Math.max(1, Math.round(w.rect.h * modal.region.h));
  const f = Math.min(1, 560 / nw);
  const canvas = $('#canvas');
  const rect = {
    x: Math.max(12, canvas.scrollLeft + 30 + Math.random() * 100),
    y: Math.max(12, canvas.scrollTop + 30 + Math.random() * 100),
    w: Math.max(MIN_W, Math.round(nw * f)),
    h: Math.max(MIN_H, Math.round(nh * f)),
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

boot();
