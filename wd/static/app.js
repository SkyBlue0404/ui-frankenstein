/* Window Dashboard — 前端主逻辑 */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);

const app = {
  locked: false,
  filter: '',
  tiles: new Map(), // id -> { cfg, el, img, dot, meta, badge, viewport }
  pollTimer: null,
};

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */

async function boot() {
  bindToolbar();
  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const state = await res.json();
    applyLayout(state.layout || {});
    $('#platform').textContent = state.platform || '—';
    syncTiles(state.tiles || []);
  } catch (err) {
    toast(`无法加载状态: ${err.message}`, true);
  }
  app.pollTimer = setInterval(pollStatus, 2000);
}

function applyLayout(layout) {
  if (layout.min_tile_px) {
    document.documentElement.style.setProperty('--tile-min', `${layout.min_tile_px}px`);
  }
  if (layout.gap_px != null) {
    document.documentElement.style.setProperty('--gap', `${layout.gap_px}px`);
  }
}

/* ------------------------------------------------------------------ */
/* 渲染                                                                */
/* ------------------------------------------------------------------ */

function syncTiles(tiles) {
  const grid = $('#grid');
  const seen = new Set();

  for (const cfg of tiles) {
    seen.add(cfg.id);
    const existing = app.tiles.get(cfg.id);
    if (existing) {
      existing.cfg = cfg;
      applyStatus(existing, cfg);
    } else {
      const entry = createTile(cfg);
      app.tiles.set(cfg.id, entry);
      grid.appendChild(entry.el);
    }
  }

  for (const [id, entry] of [...app.tiles]) {
    if (!seen.has(id)) {
      clearTimeout(entry.retryTimer);
      entry.el.remove();
      app.tiles.delete(id);
    }
  }

  $('#empty').hidden = tiles.length > 0;
  applyFilter();
}

function createTile(cfg) {
  const el = document.createElement('section');
  el.className = 'tile';
  el.dataset.id = cfg.id;
  el.innerHTML = `
    <header class="tile-head">
      <span class="dot" data-role="dot" title="未连接"></span>
      <span class="tile-title" data-role="title"></span>
      <span class="tile-meta" data-role="meta"></span>
      <button class="icon" data-role="pin" title="钉到桌面（Snipaste 风格，可拖动/缩放/点击回传）">📌</button>
      <button class="icon" data-role="focus" title="放大 / 还原 (双击)">⛶</button>
    </header>
    <div class="viewport" data-role="viewport">
      <img class="stream" data-role="img" alt="" draggable="false">
      <div class="badge waiting" data-role="badge">连接中…</div>
    </div>`;

  const entry = {
    cfg,
    el,
    img: $('[data-role=img]', el),
    dot: $('[data-role=dot]', el),
    meta: $('[data-role=meta]', el),
    badge: $('[data-role=badge]', el),
    viewport: $('[data-role=viewport]', el),
  };

  $('[data-role=title]', el).textContent = cfg.title || cfg.id;

  // MJPEG 流断开（改配置 / 服务重启 / 网络抖动）后自动重连，并做指数退避
  entry.retries = 0;
  entry.retryTimer = null;
  entry.streamUrl = () => `/stream/${encodeURIComponent(entry.cfg.id)}?t=${Date.now()}`;

  entry.img.addEventListener('load', () => {
    entry.retries = 0;
    // 让 tile 的宽高比跟着画面走，避免上下黑边
    if (entry.img.naturalWidth && entry.img.naturalHeight) {
      entry.viewport.style.aspectRatio = `${entry.img.naturalWidth} / ${entry.img.naturalHeight}`;
    }
    entry.badge.hidden = true;
  });

  entry.img.addEventListener('error', () => {
    if (!entry.el.isConnected) return;
    entry.retries += 1;
    if (entry.retries > 2) {
      entry.badge.hidden = false;
      entry.badge.className = 'badge';
      entry.badge.textContent = `画面流断开，重连中…（第 ${entry.retries} 次）`;
    }
    clearTimeout(entry.retryTimer);
    entry.retryTimer = setTimeout(() => {
      if (entry.el.isConnected) entry.img.src = entry.streamUrl();
    }, Math.min(5000, 600 * entry.retries));
  });

  entry.img.src = entry.streamUrl();

  entry.viewport.addEventListener('click', (e) => onViewportClick(e, entry));
  entry.viewport.addEventListener('contextmenu', (e) => e.preventDefault());
  entry.viewport.addEventListener('wheel', (e) => onViewportWheel(e, entry), { passive: false });
  $('[data-role=focus]', el).addEventListener('click', (e) => {
    e.stopPropagation();
    toggleFocus(entry);
  });
  $('[data-role=pin]', el).addEventListener('click', async (e) => {
    e.stopPropagation();
    await pinTile(entry.cfg.id);
  });
  el.addEventListener('dblclick', (e) => {
    e.preventDefault();
    toggleFocus(entry);
  });

  applyStatus(entry, cfg);
  return entry;
}

function applyStatus(entry, cfg) {
  entry.cfg = cfg;
  entry.dot.classList.toggle('ok', !!cfg.ok);
  entry.dot.classList.toggle('err', !cfg.ok && !!cfg.error);
  entry.dot.title = cfg.ok ? '正常' : (cfg.error || '未连接');

  const bits = [];
  if (cfg.ok) bits.push(`${cfg.fps.toFixed(0)} fps`);
  if (cfg.crop_size && cfg.crop_size[0]) bits.push(`${cfg.crop_size[0]}×${cfg.crop_size[1]}`);
  entry.meta.textContent = bits.join(' · ');

  const locked = app.locked || cfg.interactive === false;
  entry.el.classList.toggle('locked', locked);

  if (cfg.error) {
    entry.badge.hidden = false;
    entry.badge.className = 'badge';
    entry.badge.textContent = cfg.error;
  } else if (cfg.ok) {
    entry.badge.hidden = true;
  } else {
    entry.badge.hidden = false;
    entry.badge.className = 'badge waiting';
    entry.badge.textContent = '连接中…';
  }
}

async function pollStatus() {
  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    if (!res.ok) return;
    const state = await res.json();
    for (const cfg of state.tiles || []) {
      const entry = app.tiles.get(cfg.id);
      if (entry) applyStatus(entry, cfg);
    }
  } catch {
    /* 静默：下一轮再试 */
  }
}

/* ------------------------------------------------------------------ */
/* 点击 / 滚轮回传                                                     */
/* ------------------------------------------------------------------ */

/**
 * 把浏览器里的点击坐标换算成「裁剪图上的像素坐标」。
 * 图片是 object-fit: contain，所以要先去掉黑边再除以缩放比。
 */
function mapPoint(evt, img) {
  const rect = img.getBoundingClientRect();
  const nw = img.naturalWidth || 0;
  const nh = img.naturalHeight || 0;
  if (!nw || !nh || !rect.width || !rect.height) return null;

  const scale = Math.min(rect.width / nw, rect.height / nh);
  const dispW = nw * scale;
  const dispH = nh * scale;
  const offX = (rect.width - dispW) / 2;
  const offY = (rect.height - dispH) / 2;

  const x = (evt.clientX - rect.left - offX) / scale;
  const y = (evt.clientY - rect.top - offY) / scale;
  if (x < 0 || y < 0 || x > nw || y > nh) return null;
  return { x, y };
}

function isInteractive(entry) {
  return !app.locked && entry.cfg.interactive !== false;
}

async function onViewportClick(evt, entry) {
  if (!isInteractive(entry)) {
    if (app.locked) toast('已锁定，点击不会回传（顶栏可解锁）');
    return;
  }
  const p = mapPoint(evt, entry.img);
  if (!p) return;
  await sendInput(entry.cfg.id, {
    type: 'click',
    x: p.x,
    y: p.y,
    button: evt.button + 1,
    double: false,
  });
}

let wheelPending = 0;
let wheelTimer = null;

function onViewportWheel(evt, entry) {
  if (!isInteractive(entry)) return;
  evt.preventDefault();
  const p = mapPoint(evt, entry.img);
  if (!p) return;

  wheelPending += evt.deltaY < 0 ? 1 : -1;
  if (wheelTimer) return;
  wheelTimer = setTimeout(async () => {
    const delta = wheelPending;
    wheelPending = 0;
    wheelTimer = null;
    const anchor = mapPoint(evt, entry.img) || p;
    await sendInput(entry.cfg.id, { type: 'scroll', x: anchor.x, y: anchor.y, delta });
  }, 80);
}

/** 在服务器那台机器上弹出一个贴图窗口（Snipaste 风格）。 */
async function pinTile(tileId) {
  try {
    const res = await fetch(`/api/tiles/${encodeURIComponent(tileId)}/pin`, {
      method: 'POST',
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    toast('已贴到桌面，拖拽可移动，右键有菜单');
  } catch (err) {
    toast(`贴图失败: ${err.message}`, true);
  }
}

async function sendInput(tileId, body) {  try {
    const res = await fetch(`/api/tiles/${encodeURIComponent(tileId)}/input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      toast(detail.detail || `输入失败: HTTP ${res.status}`, true);
    }
  } catch (err) {
    toast(`输入失败: ${err.message}`, true);
  }
}

/* ------------------------------------------------------------------ */
/* 工具栏 / 交互辅助                                                   */
/* ------------------------------------------------------------------ */

function bindToolbar() {
  const lockBtn = $('#btn-lock');
  lockBtn.addEventListener('click', () => {
    app.locked = !app.locked;
    lockBtn.textContent = app.locked ? '🔒 已锁定' : '🔓 可操作';
    lockBtn.classList.toggle('on', app.locked);
    for (const entry of app.tiles.values()) applyStatus(entry, entry.cfg);
  });

  $('#btn-reload').addEventListener('click', async () => {
    try {
      await fetch('/api/reload', { method: 'POST' });
      await refresh();
      toast('配置已重载');
    } catch (err) {
      toast(`重载失败: ${err.message}`, true);
    }
  });

  $('#filter').addEventListener('input', (e) => {
    app.filter = e.target.value.trim().toLowerCase();
    applyFilter();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const focused = [...app.tiles.values()].find((t) => t.el.classList.contains('focused'));
    if (focused) toggleFocus(focused);
  });
}

function applyFilter() {
  for (const entry of app.tiles.values()) {
    const hay = `${entry.cfg.title || ''} ${entry.cfg.id}`.toLowerCase();
    entry.el.style.display = !app.filter || hay.includes(app.filter) ? '' : 'none';
  }
}

function toggleFocus(entry) {
  const willFocus = !entry.el.classList.contains('focused');
  for (const other of app.tiles.values()) other.el.classList.remove('focused');
  if (willFocus) entry.el.classList.add('focused');
}

async function refresh() {
  const res = await fetch('/api/state', { cache: 'no-store' });
  const state = await res.json();
  applyLayout(state.layout || {});
  $('#platform').textContent = state.platform || '—';
  syncTiles(state.tiles || []);
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = isErr ? 'toast err' : 'toast';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 2600);
}

boot();
