/* 编辑布局页 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);

const s = {
  windows: [],
  selected: null,     // 当前选中的窗口
  editingId: null,    // 正在编辑的 tile id（null = 新建）
  region: { x: 0, y: 0, w: 1, h: 1 },
  dragStart: null,
};

/* ------------------------------------------------------------------ */
/* 初始化                                                              */
/* ------------------------------------------------------------------ */

async function boot() {
  $('#btn-refresh').addEventListener('click', loadWindows);
  $('#win-filter').addEventListener('input', renderWindows);
  $('#btn-clear').addEventListener('click', () => resetForm());
  $('#tile-form').addEventListener('submit', onSave);
  bindDrag();

  await Promise.all([loadWindows(), loadTiles()]);
}

/* ------------------------------------------------------------------ */
/* 窗口列表                                                            */
/* ------------------------------------------------------------------ */

async function loadWindows() {
  try {
    const res = await fetch('/api/windows', { cache: 'no-store' });
    s.windows = await res.json();
    renderWindows();
  } catch (err) {
    toast(`枚举窗口失败: ${err.message}`, true);
  }
}

function renderWindows() {
  const q = $('#win-filter').value.trim().toLowerCase();
  const list = $('#win-list');
  list.innerHTML = '';

  const items = s.windows.filter((w) => {
    if (!q) return true;
    return w.title.toLowerCase().includes(q) || (w.app || '').toLowerCase().includes(q);
  });

  if (!items.length) {
    const li = document.createElement('li');
    li.className = 'w-app';
    li.textContent = s.windows.length ? '没有匹配的窗口' : '没有可见窗口';
    list.appendChild(li);
    return;
  }

  for (const w of items) {
    const li = document.createElement('li');
    li.classList.toggle('active', s.selected && s.selected.handle === w.handle);
    li.innerHTML = `<span class="w-title"></span><span class="w-app"></span>`;
    $('.w-title', li).textContent = w.title;
    $('.w-app', li).textContent = `${w.app || '—'} · ${w.rect.w}×${w.rect.h}`;
    li.addEventListener('click', () => selectWindow(w));
    list.appendChild(li);
  }
}

async function selectWindow(w) {
  s.selected = w;
  renderWindows();
  $('#stage-title').textContent = w.title;
  $('#stage-hint').textContent = `${w.app || '—'} · 在截图上拖拽框选区域`;

  const shot = $('#shot');
  const img = $('#shot-img');
  $('.stage-empty') && $('.stage-empty').remove();

  try {
    const url = `/api/snapshot?handle=${encodeURIComponent(w.handle)}&max_width=2400&t=${Date.now()}`;
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error('截图加载失败'));
      img.src = url;
    });
    shot.hidden = false;
    $('#sel').hidden = false;
  } catch (err) {
    shot.hidden = true;
    toast(`抓取截图失败: ${err.message}（可试试 window/screen 抓取模式）`, true);
    return;
  }

  if (!s.editingId) {
    s.region = { x: 0, y: 0, w: 1, h: 1 };
  }
  drawSel();
  syncForm();
}

/* ------------------------------------------------------------------ */
/* 框选                                                                */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* 框选：新建 / 拖动 / 8 向缩放                                          */
/* ------------------------------------------------------------------ */

const HANDLE_HIT = 9; // 手柄命中半径（CSS 像素）

function bindDrag() {
  const shot = $('#shot');
  const sel = $('#sel');
  if (!shot || !sel) return;

  const toNorm = (evt) => {
    const img = $('#shot-img');
    const r = img.getBoundingClientRect();
    return {
      x: clamp01((evt.clientX - r.left) / r.width),
      y: clamp01((evt.clientY - r.top) / r.height),
    };
  };

  const toPx = (evt) => {
    const r = $('#shot-img').getBoundingClientRect();
    return { x: evt.clientX - r.left, y: evt.clientY - r.top, w: r.width, h: r.height };
  };

  /** 指针落在哪个手柄上；不在手柄上返回 ''。选框本身 pointer-events:none，靠坐标判定。 */
  const hitHandle = (evt) => {
    const p = toPx(evt);
    const hx = s.region.x * p.w, hy = s.region.y * p.h;
    const hw = s.region.w * p.w, hh = s.region.h * p.h;
    const nearL = Math.abs(p.x - hx) <= HANDLE_HIT;
    const nearR = Math.abs(p.x - (hx + hw)) <= HANDLE_HIT;
    const nearT = Math.abs(p.y - hy) <= HANDLE_HIT;
    const nearB = Math.abs(p.y - (hy + hh)) <= HANDLE_HIT;
    const inX = p.x >= hx - HANDLE_HIT && p.x <= hx + hw + HANDLE_HIT;
    const inY = p.y >= hy - HANDLE_HIT && p.y <= hy + hh + HANDLE_HIT;
    if (!inX || !inY) return '';
    if (nearT && nearL) return 'nw';
    if (nearT && nearR) return 'ne';
    if (nearB && nearL) return 'sw';
    if (nearB && nearR) return 'se';
    if (nearT) return 'n';
    if (nearB) return 's';
    if (nearL) return 'w';
    if (nearR) return 'e';
    return '';
  };

  const insideSel = (evt) => {
    const p = toNorm(evt);
    return p.x > s.region.x && p.x < s.region.x + s.region.w &&
           p.y > s.region.y && p.y < s.region.y + s.region.h;
  };

  /** 选区还是「整张图」——此时点哪儿都算内部，应该允许重新框选。 */
  const isFullFrame = () =>
    s.region.x < 0.005 && s.region.y < 0.005 &&
    s.region.w > 0.99 && s.region.h > 0.99;

  let mode = '';   // 'new' | 'move' | 'resize'
  let dir = '';
  let anchor = null;
  let base = null;

  shot.addEventListener('pointerdown', (evt) => {
    if (!s.selected) return;
    evt.preventDefault();
    try { shot.setPointerCapture(evt.pointerId); } catch { /* 合成事件没有真实 pointerId */ }

    anchor = toNorm(evt);
    base = { ...s.region };

    const handle = sel.hidden ? '' : hitHandle(evt);
    if (handle) {
      mode = 'resize';
      dir = handle;
      shot.classList.add('resizing');
    } else if (!sel.hidden && !isFullFrame() && insideSel(evt)) {
      mode = 'move';
      shot.classList.add('moving');
    } else {
      mode = 'new';
      s.region = { x: anchor.x, y: anchor.y, w: 0, h: 0 };
      drawSel();
    }
  });

  shot.addEventListener('pointermove', (evt) => {
    if (!mode) return;
    const p = toNorm(evt);
    if (mode === 'new') {
      s.region = rectOf(anchor, p);
    } else if (mode === 'move') {
      const nx = clamp01(Math.min(1 - base.w, Math.max(0, base.x + (p.x - anchor.x))));
      const ny = clamp01(Math.min(1 - base.h, Math.max(0, base.y + (p.y - anchor.y))));
      s.region = { x: nx, y: ny, w: base.w, h: base.h };
    } else {
      s.region = resizeRect(base, dir, p);
    }
    drawSel();
  });

  const finish = (evt) => {
    if (!mode) return;
    try { shot.releasePointerCapture(evt.pointerId); } catch { /* ignore */ }
    if (mode === 'new' && (s.region.w < 0.004 || s.region.h < 0.004)) {
      s.region = base; // 基本等于单击 —— 保留原区域
    }
    mode = '';
    dir = '';
    shot.classList.remove('moving', 'resizing');
    drawSel();
    syncForm();
  };

  shot.addEventListener('pointerup', finish);
  shot.addEventListener('pointercancel', finish);
}

/** 按方向把手柄拖到 p 之后的新矩形（带最小尺寸，且不越界）。 */
function resizeRect(base, dir, p) {
  const MIN = 0.008;
  let x = base.x;
  let y = base.y;
  let x2 = base.x + base.w;
  let y2 = base.y + base.h;
  if (dir.includes('w')) x = Math.min(p.x, x2 - MIN);
  if (dir.includes('e')) x2 = Math.max(p.x, x + MIN);
  if (dir.includes('n')) y = Math.min(p.y, y2 - MIN);
  if (dir.includes('s')) y2 = Math.max(p.y, y + MIN);
  x = clamp01(x);
  y = clamp01(y);
  x2 = clamp01(x2);
  y2 = clamp01(y2);
  return { x, y, w: x2 - x, h: y2 - y };
}

function rectOf(a, b) {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    w: Math.abs(a.x - b.x),
    h: Math.abs(a.y - b.y),
  };
}

function drawSel() {
  const sel = $('#sel');
  const img = $('#shot-img');
  if (!sel || !img || sel.hidden) return;
  const W = img.clientWidth;
  const H = img.clientHeight;
  sel.style.left = `${s.region.x * W}px`;
  sel.style.top = `${s.region.y * H}px`;
  sel.style.width = `${Math.max(1, s.region.w * W)}px`;
  sel.style.height = `${Math.max(1, s.region.h * H)}px`;
  updateCoords();
}

function updateCoords() {
  const img = $('#shot-img');
  const W = img.naturalWidth || 0;
  const H = img.naturalHeight || 0;
  const { x, y, w, h } = s.region;
  $('#coords').textContent =
    `区域：x=${x.toFixed(4)} y=${y.toFixed(4)} w=${w.toFixed(4)} h=${h.toFixed(4)}` +
    (W ? `  ·  约 ${Math.round(w * W)}×${Math.round(h * H)} px` : '');
}

/* ------------------------------------------------------------------ */
/* 表单                                                                */
/* ------------------------------------------------------------------ */

function syncForm() {
  const f = $('#tile-form');
  f.rx.value = s.region.x.toFixed(4);
  f.ry.value = s.region.y.toFixed(4);
  f.rw.value = s.region.w.toFixed(4);
  f.rh.value = s.region.h.toFixed(4);
  updateCoords();
}

function readFormRegion() {
  const f = $('#tile-form');
  const nums = ['rx', 'ry', 'rw', 'rh'].map((n) => parseFloat(f[n].value));
  if (nums.some((v) => Number.isNaN(v))) return s.region;
  const [x, y, w, h] = nums.map(clamp01);
  return { x, y, w: Math.max(0.001, w), h: Math.max(0.001, h) };
}

function resetForm() {
  $('#tile-form').reset();
  s.editingId = null;
  s.region = { x: 0, y: 0, w: 1, h: 1 };
  $('#tile-form').id.readOnly = false;
  drawSel();
  syncForm();
  $('#form-hint').textContent = '';
}

async function onSave(evt) {
  evt.preventDefault();
  const f = $('#tile-form');
  const id = f.id.value.trim();
  if (!id) return toast('ID 不能为空', true);

  s.region = readFormRegion();

  const titleRegex = f.title_regex.value.trim() || (s.selected ? `^${escapeRe(s.selected.title)}$` : '');
  if (!titleRegex) return toast('请先在左侧选一个窗口，或填标题正则', true);

  const payload = {
    id,
    title: f.title.value.trim() || id,
    window: { handle: '', title_regex: titleRegex, app_regex: '', index: 0 },
    region: s.region,
    fps: parseFloat(f.fps.value) || 8,
    quality: parseInt(f.quality.value, 10) || 80,
    capture: f.capture.value,
    interactive: f.interactive.checked,
  };

  try {
    const res = await fetch(`/api/tiles/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${res.status}`);
    }
    if (s.editingId && s.editingId !== id) {
      await fetch(`/api/tiles/${encodeURIComponent(s.editingId)}`, { method: 'DELETE' });
    }
    s.editingId = id;
    f.id.readOnly = true;
    $('#form-hint').textContent = `已保存，标题正则：${titleRegex}`;
    toast('已保存');
    await loadTiles();
  } catch (err) {
    toast(`保存失败: ${err.message}`, true);
  }
}

/* ------------------------------------------------------------------ */
/* 已有 tile                                                           */
/* ------------------------------------------------------------------ */

async function loadTiles() {
  const res = await fetch('/api/config', { cache: 'no-store' });
  const cfg = await res.json();
  const list = $('#tile-list');
  list.innerHTML = '';

  if (!cfg.tiles || !cfg.tiles.length) {
    const li = document.createElement('li');
    li.className = 't-id';
    li.textContent = '暂无';
    list.appendChild(li);
    return;
  }

  for (const t of cfg.tiles) {
    const li = document.createElement('li');
    li.innerHTML = `<span class="t-name"></span><span class="t-id"></span><button class="del" title="删除">✕</button>`;
    $('.t-name', li).textContent = t.title || t.id;
    $('.t-id', li).textContent = t.id;

    li.addEventListener('click', (e) => {
      if (e.target.classList.contains('del')) return;
      editTile(t);
    });
    $('.del', li).addEventListener('click', async (e) => {
      e.stopPropagation();
      await fetch(`/api/tiles/${encodeURIComponent(t.id)}`, { method: 'DELETE' });
      if (s.editingId === t.id) resetForm();
      toast(`已删除 ${t.id}`);
      await loadTiles();
    });

    list.appendChild(li);
  }
}

function editTile(t) {
  s.editingId = t.id;
  s.region = { ...t.region };
  const f = $('#tile-form');
  f.id.value = t.id;
  f.id.readOnly = true;
  f.title.value = t.title || '';
  f.fps.value = t.fps;
  f.quality.value = t.quality;
  f.capture.value = t.capture || 'window';
  f.interactive.checked = t.interactive !== false;
  f.title_regex.value = (t.window && t.window.title_regex) || '';
  syncForm();
  $('#form-hint').textContent = `正在编辑 ${t.id}`;

  const win = s.windows.find((w) => w.handle === (t.window && t.window.handle));
  if (win) {
    selectWindow(win);
  } else if (!s.selected && (t.window && t.window.title_regex)) {
    // 尝试用正则反查一个窗口，方便重新框选
    try {
      const re = new RegExp(t.window.title_regex);
      const match = s.windows.find((w) => re.test(w.title));
      if (match) selectWindow(match);
    } catch { /* 无效正则，忽略 */ }
  }
}

/* ------------------------------------------------------------------ */
/* 工具                                                                */
/* ------------------------------------------------------------------ */

function clamp01(v) { return Math.max(0, Math.min(1, v)); }
function escapeRe(str) { return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = isErr ? 'toast err' : 'toast';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3000);
}

window.addEventListener('resize', () => drawSel());

boot();
