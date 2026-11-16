from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .capture import TileWorker
from .config import Config
from .platform import get_backend, get_hotkey_listener


def _assets_dir() -> Path:
    """素材目录（HTML / CSS / JS）。

    源码布局：<root>/assets        （本文件在 <root>/src/frankenstein/）
    冻结布局：<_MEIPASS>/assets
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[2]
    return base / "assets"


ASSETS_DIR = _assets_dir()
HTML_DIR = ASSETS_DIR / "html"
_BOUNDARY = b"--frame\r\n"

_ASSET_RE = re.compile(r"/static/([A-Za-z0-9_./-]+\.(?:css|js))")


def _render_html(name: str) -> HTMLResponse:
    """读模板并给静态资源加 mtime 版本号 —— 改了前端代码刷新就生效，不用清缓存。"""
    path = HTML_DIR / name
    html = path.read_text("utf-8")

    def _repl(m: re.Match[str]) -> str:
        rel = m.group(1)
        try:
            version = int((ASSETS_DIR / rel).stat().st_mtime)
        except OSError:
            version = 0
        return f"/static/{rel}?v={version}"

    return HTMLResponse(
        _ASSET_RE.sub(_repl, html),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class InputBody(BaseModel):
    type: str = Field(default="click", pattern="^(click|scroll)$")
    x: float = 0.0
    y: float = 0.0
    button: int = 1
    double: bool = False
    delta: int = 0


class ReorderBody(BaseModel):
    ids: list[str]


class CanvasBody(BaseModel):
    x: float = 40.0
    y: float = 40.0
    w: float = 480.0
    h: float = 300.0


class RegionUpdate(BaseModel):
    """二次调整裁剪区域（窗口内归一化坐标）。"""

    x: float
    y: float
    w: float
    h: float


class HotkeyBody(BaseModel):
    hotkey: str


class ClickModeBody(BaseModel):
    mode: str


_HOTKEY_RE = re.compile(
    r"^(?:(?:ctrl|control|alt|shift|super|win|meta)\+)*"
    r"(?:[a-z0-9]|f[1-9]|f1[0-9]|f2[0-4]|space|tab|enter|escape|insert|delete|home|end|"
    r"pageup|pagedown|up|down|left|right|minus|equal|comma|period|slash|backslash|"
    r"grave|bracketleft|bracketright|semicolon|apostrophe)$",
    re.IGNORECASE,
)


def _valid_hotkey(spec: str) -> bool:
    parts = [p for p in spec.split("+") if p.strip()]
    if not parts:
        return False
    # 允许单键（F9、space 等）；单字母会拦截正常输入，前端会额外提醒
    return bool(_HOTKEY_RE.match("+".join(p.strip().lower() for p in parts)))


class RegionBody(BaseModel):
    """全屏框选提交的屏幕坐标矩形。"""

    handle: str
    x: int
    y: int
    w: int
    h: int


# ---------------------------------------------------------------------------
# 运行时状态
# ---------------------------------------------------------------------------


class Runtime:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.backend = get_backend()
        self.backend.click_mode = str(cfg.defaults.get("click_mode", "event"))
        self.workers: dict[str, TileWorker] = {}
        # 模式 A 的瞬时 tile：快捷键截取的窗口，不写进配置文件
        self.transient: dict[str, dict[str, Any]] = {}
        self.hotkey: Any = None
        self._hotkey_cb: Any = None
        self.lock = threading.RLock()

    # ---- tile 集合 ----

    def all_tiles(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.cfg.tiles) + list(self.transient.values())

    def start(self) -> None:
        with self.lock:
            self._stop_all()
            for tile in self.all_tiles():
                worker = TileWorker(tile, self.backend)
                worker.start()
                self.workers[tile["id"]] = worker

    def _stop_all(self) -> None:
        for w in self.workers.values():
            w.stop()
        self.workers.clear()

    def restart(self) -> None:
        self.start()

    def sync(self) -> None:
        """增量同步：只重启配置变了的 tile，其余画面流保持不断。"""
        with self.lock:
            wanted = {t["id"]: t for t in self.all_tiles()}
            for tid, worker in list(self.workers.items()):
                if tid not in wanted or worker.cfg != wanted[tid]:
                    worker.stop()
                    del self.workers[tid]
            for tid, tile in wanted.items():
                if tid not in self.workers:
                    worker = TileWorker(tile, self.backend)
                    worker.start()
                    self.workers[tid] = worker

    # ---- 全局快捷键 ----

    def restart_hotkey(self, spec: str) -> bool:
        """换一个全局快捷键：停掉旧的监听器，按新键重新 grab。

        返回**真实的注册结果**——被别的程序占用时 grab 会失败，
        不能只看 start() 有没有抛异常。
        """
        with self.lock:
            old = self.hotkey
            self.hotkey = None
            if old is not None:
                try:
                    old.stop()
                    # 等旧连接关掉、把 grab 释放掉，否则新 grab 会撞 BadAccess
                    old.join(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass

            if self._hotkey_cb is None:
                return False
            try:
                hk = get_hotkey_listener(self._hotkey_cb, spec)
            except Exception:  # noqa: BLE001
                return False
            if hk is None:
                return False

            hk.start()
            ready = getattr(hk, "ready", None)
            if ready is not None:
                ready.wait(timeout=2.0)
            if not bool(getattr(hk, "ok", False)):
                try:
                    hk.stop()
                except Exception:  # noqa: BLE001
                    pass
                return False

            self.hotkey = hk
            return True

    # ---- 瞬时 tile（模式 A） ----
    def add_transient(
        self, handle: str, title: str, region: dict[str, float] | None = None
    ) -> dict[str, Any]:
        with self.lock:
            base = f"pin-{int(time.time() * 1000) % 1_000_000}"
            tid = base
            n = 1
            while tid in self.transient or self.cfg.tile(tid) is not None:
                n += 1
                tid = f"{base}-{n}"

            tile = {
                "id": tid,
                "title": title or tid,
                "window": {
                    "handle": str(handle),
                    "title_regex": "",
                    "app_regex": "",
                    "index": 0,
                },
                "region": region or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
                "fps": float(self.cfg.defaults.get("fps", 12)),
                "quality": int(self.cfg.defaults.get("quality", 80)),
                "capture": str(self.cfg.defaults.get("capture", "window")),
                "interactive": True,
                "canvas": None,
            }
            self.transient[tid] = tile
            worker = TileWorker(tile, self.backend)
            worker.start()
            self.workers[tid] = worker
            return tile

    def remove_transient(self, tile_id: str) -> bool:
        with self.lock:
            if tile_id not in self.transient:
                return False
            self.transient.pop(tile_id, None)
            worker = self.workers.pop(tile_id, None)
            if worker is not None:
                worker.stop()
            return True

    # ---- 杂项 ----

    def worker(self, tile_id: str) -> TileWorker:
        with self.lock:
            w = self.workers.get(tile_id)
        if w is None:
            raise HTTPException(404, f"unknown tile: {tile_id}")
        return w

    def close(self) -> None:
        with self.lock:
            if self.hotkey is not None:
                try:
                    self.hotkey.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._stop_all()
            self.backend.close()


# ---------------------------------------------------------------------------
# MJPEG
# ---------------------------------------------------------------------------


def _mjpeg(worker: TileWorker) -> Iterator[bytes]:
    last = -1
    try:
        while True:
            if worker.stopped:
                return  # 配置变更 → 主动关流，让浏览器重连到新 worker
            seq, jpeg = worker.wait_frame(last, timeout=5.0)
            if worker.stopped:
                return
            if jpeg is None:
                # 还没抓到第一帧 / 出错，等下一轮
                continue
            if seq == last:
                continue
            last = seq
            yield (
                _BOUNDARY
                + b"Content-Type: image/jpeg\r\n"
                + b"Content-Length: "
                + str(len(jpeg)).encode()
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
    except GeneratorExit:  # 客户端断开
        return


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def create_app(config_path: str | Path) -> FastAPI:
    cfg = Config(config_path)
    rt = Runtime(cfg)
    rt.start()

    app = FastAPI(title="Frankenstein", version="0.1.0")
    app.state.runtime = rt

    @app.middleware("http")
    async def _no_cache_assets(request: Request, call_next):  # type: ignore[no-untyped-def]
        """前端素材禁缓存：本地工具，改了代码刷新就该看到。"""
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    # ---- 模式 A：全局快捷键截取当前聚焦窗口 ----

    def _spawn_pin(tile_id: str, own: bool = False) -> None:
        base = f"http://127.0.0.1:{int(cfg.server.get('port', 8777))}"
        extra = ["--own-tile"] if own else []
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--pin", tile_id, "--server", base, *extra]
        else:
            cmd = [
                sys.executable,
                str(Path(__file__).resolve().parents[2] / "run.py"),
                "--pin", tile_id, "--server", base, *extra,
            ]
        subprocess.Popen(cmd, start_new_session=True)

    def _spawn_selector(handle: str) -> None:
        """拉起全屏框选遮罩（Snipaste 式），选完再贴。"""
        base = f"http://127.0.0.1:{int(cfg.server.get('port', 8777))}"
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--select", "--handle", str(handle), "--server", base]
        else:
            cmd = [
                sys.executable,
                str(Path(__file__).resolve().parents[2] / "run.py"),
                "--select", "--handle", str(handle), "--server", base,
            ]
        subprocess.Popen(cmd, start_new_session=True)

    def _on_hotkey() -> None:
        try:
            win = rt.backend.focused_window()
            if win is None:
                print("[!] 快捷键：没有找到聚焦窗口", file=sys.stderr)
                return
            _spawn_selector(win.handle)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] 快捷键处理失败: {exc}", file=sys.stderr)

    rt._hotkey_cb = _on_hotkey
    if not rt.restart_hotkey(cfg.defaults.get("hotkey", "ctrl+alt+p")):
        print("[!] 全局快捷键不可用（可能被占用或被系统限制）", file=sys.stderr)

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover
        rt.close()

    # ---- 页面 ----

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return _render_html("canvas.html")

    @app.get("/setup", include_in_schema=False)
    def setup_page() -> FileResponse:
        return _render_html("setup.html")

    @app.get("/pins", include_in_schema=False)
    def pins_page() -> FileResponse:
        return _render_html("pins.html")

    # ---- 状态 ----

    @app.get("/api/state")
    def api_state() -> dict[str, Any]:
        with rt.lock:
            workers = list(rt.workers.values())
        return {
            "platform": rt.backend.name,
            "layout": cfg.layout,
            "hotkey": cfg.defaults.get("hotkey", "ctrl+alt+p"),
            "click_mode": getattr(rt.backend, "click_mode", "event"),
            "tiles": [w.status() for w in workers],
        }

    @app.get("/api/windows")
    def api_windows(q: str = Query(default="")) -> list[dict[str, Any]]:
        windows = rt.backend.list_windows()
        needle = q.lower().strip()
        out = []
        for w in windows:
            if needle and needle not in w.title.lower() and needle not in (w.app or "").lower():
                continue
            out.append(w.as_dict())
        return out

    @app.get("/api/snapshot")
    def api_snapshot(
        handle: str = Query(...),
        max_width: int = Query(default=1600, ge=200, le=4096),
    ) -> Response:
        try:
            img = rt.backend.grab_window(handle, mode="window")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"capture failed: {exc}") from exc
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, max(1, int(img.height * ratio))))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    # ---- 配置 ----

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return cfg.data

    # ---- 模式 B：画布布局 ----

    @app.put("/api/tiles/{tile_id}/canvas")
    def api_canvas(tile_id: str, body: CanvasBody) -> dict[str, Any]:
        rect = {
            "x": round(float(body.x), 1),
            "y": round(float(body.y), 1),
            "w": round(max(40.0, float(body.w)), 1),
            "h": round(max(20.0, float(body.h)), 1),
        }
        tile = cfg.tile(tile_id)
        if tile is None:
            # 瞬时 tile 的位置只留在内存里
            with rt.lock:
                transient = rt.transient.get(tile_id)
                if transient is None:
                    raise HTTPException(404, f"unknown tile: {tile_id}")
                transient["canvas"] = rect
            return {"ok": True, "persisted": False}
        tile["canvas"] = rect
        cfg.save()
        return {"ok": True, "persisted": True}

    @app.put("/api/tiles/{tile_id}/region")
    def api_region(tile_id: str, body: RegionUpdate) -> dict[str, Any]:
        """二次调整裁剪区域：改了之后立刻重启这个 tile 的抓帧线程。"""
        x = max(0.0, min(1.0, float(body.x)))
        y = max(0.0, min(1.0, float(body.y)))
        w = max(0.002, min(1.0 - x, float(body.w)))
        h = max(0.002, min(1.0 - y, float(body.h)))
        region = {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}

        tile = cfg.tile(tile_id)
        if tile is None:
            with rt.lock:
                transient = rt.transient.get(tile_id)
                if transient is None:
                    raise HTTPException(404, f"unknown tile: {tile_id}")
                transient["region"] = region
            rt.sync()
            return {"ok": True, "persisted": False, "region": region}

        tile["region"] = region
        cfg.save()
        rt.sync()
        return {"ok": True, "persisted": True, "region": region}

    # ---- 模式 A：区域截取 ----

    @app.post("/api/quit")
    def api_quit() -> dict[str, Any]:
        """退出后台进程（守护模式下没有窗口，用这个关）。"""
        threading.Timer(0.4, lambda: os._exit(0)).start()
        return {"ok": True}

    @app.put("/api/hotkey")
    def api_hotkey(body: HotkeyBody) -> dict[str, Any]:
        """改全局快捷键，即时生效并写回配置。"""
        spec = body.hotkey.strip().lower()
        if not _valid_hotkey(spec):
            raise HTTPException(
                400,
                f"不支持这个按键「{body.hotkey}」。可用：字母/数字、F1~F24、方向键、"
                "space/tab/enter/delete 等，可加 Ctrl/Alt/Shift/Super 组合",
            )
        if not rt.restart_hotkey(spec):
            raise HTTPException(
                500, f"注册失败：{spec} 可能已被其他程序占用，换一个组合试试"
            )
        cfg.data["defaults"]["hotkey"] = spec
        cfg.save()
        return {"ok": True, "hotkey": spec}

    @app.put("/api/click-mode")
    def api_click_mode(body: ClickModeBody) -> dict[str, Any]:
        """切换点击注入方式，立即生效。

        event    —— 直接把事件投递给窗口（不动物理鼠标），
                    但部分 Electron/Qt 程序会丢弃合成事件
        physical —— 真实的鼠标注入（XTest），兼容性最好；
                    会短暂挪动指针，点完自动挪回原处
        """
        mode = body.mode.strip().lower()
        if mode not in ("event", "physical"):
            raise HTTPException(400, "mode 只能是 'event' 或 'physical'")
        rt.backend.click_mode = mode
        cfg.data["defaults"]["click_mode"] = mode
        cfg.save()
        print(f"[i] 点击方式切换为: {mode}")
        return {"ok": True, "mode": mode}

    @app.post("/api/capture-focus")
    def api_capture_focus() -> dict[str, Any]:
        """打开全屏框选遮罩（等同于按快捷键）。"""
        win = rt.backend.focused_window()
        if win is None:
            raise HTTPException(404, "没有找到聚焦窗口")
        try:
            _spawn_selector(win.handle)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"拉起框选失败: {exc}") from exc
        return {"ok": True, "handle": win.handle, "title": win.title}

    @app.post("/api/capture-region")
    def api_capture_region(body: RegionBody) -> dict[str, Any]:
        """遮罩选完区域后回传屏幕坐标，转成窗口内归一化区域并贴图。"""
        try:
            rect = rt.backend.window_rect(body.handle)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"窗口已失效: {exc}") from exc
        if rect.w <= 0 or rect.h <= 0:
            raise HTTPException(400, "窗口尺寸无效")

        # 屏幕坐标 -> 窗口内归一化，并夹到 [0, 1]
        nx = (body.x - rect.x) / rect.w
        ny = (body.y - rect.y) / rect.h
        nw = body.w / rect.w
        nh = body.h / rect.h
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        nw = max(0.002, min(1.0 - nx, nw))
        nh = max(0.002, min(1.0 - ny, nh))
        region = {
            "x": round(nx, 6), "y": round(ny, 6),
            "w": round(nw, 6), "h": round(nh, 6),
        }

        win = next(
            (w for w in rt.backend.list_windows() if int(w.handle) == int(body.handle)),
            None,
        )
        title = win.title if win else f"区域 {body.w}×{body.h}"
        tile = rt.add_transient(body.handle, title, region=region)
        try:
            _spawn_pin(tile["id"], own=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"拉起贴图失败: {exc}") from exc
        print(f"[i] 已贴图: {title[:40]}  区域 {body.w}×{body.h}")
        return {"ok": True, "id": tile["id"], "title": title, "region": region}

    @app.post("/api/reload")
    def api_reload() -> dict[str, Any]:
        cfg.load()
        rt.sync()
        return {"ok": True, "tiles": len(cfg.tiles)}

    @app.put("/api/tiles/{tile_id}")
    def api_put_tile(tile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "id": tile_id}
        try:
            cfg.upsert_tile(payload)
            cfg.save()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        rt.sync()
        return {"ok": True}

    @app.delete("/api/tiles/{tile_id}")
    def api_delete_tile(tile_id: str) -> dict[str, Any]:
        # 瞬时 tile（模式 A 快捷键截取的）只从运行时移除
        if rt.remove_transient(tile_id):
            return {"ok": True}
        if not cfg.remove_tile(tile_id):
            raise HTTPException(404, f"unknown tile: {tile_id}")
        cfg.save()
        rt.sync()
        return {"ok": True}

    @app.post("/api/tiles/reorder")
    def api_reorder(body: ReorderBody) -> dict[str, Any]:
        cfg.reorder(body.ids)
        cfg.save()
        rt.sync()
        return {"ok": True}

    # ---- 输入 ----

    @app.get("/api/tiles/{tile_id}/frame.jpg")
    def api_frame(tile_id: str) -> Response:
        """贴图窗口轮询用的单帧接口。"""
        worker = rt.worker(tile_id)
        jpeg = worker.latest_jpeg()
        if jpeg is None:
            raise HTTPException(503, "no frame yet")
        return Response(
            content=jpeg,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, no-cache"},
        )

    @app.post("/api/tiles/{tile_id}/pin")
    def api_pin(tile_id: str, request: Request, scale: float = 1.0) -> dict[str, Any]:
        """在本机弹出一个 Snipaste 风格的贴图窗口。"""
        rt.worker(tile_id)  # 不存在则 404
        base = str(request.base_url).rstrip("/")
        pin_scale = max(0.2, min(6.0, float(scale)))

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--pin", tile_id, "--server", base, "--scale", str(pin_scale)]
        else:
            cmd = [
                sys.executable,
                str(Path(__file__).resolve().parents[2] / "run.py"),
                "--pin", tile_id,
                "--server", base,
                "--scale", str(pin_scale),
            ]
        try:
            subprocess.Popen(cmd, start_new_session=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"启动贴图失败: {exc}") from exc
        return {"ok": True, "tile": tile_id, "scale": pin_scale}

    @app.post("/api/tiles/{tile_id}/input")
    def api_input(tile_id: str, body: InputBody) -> dict[str, Any]:
        worker = rt.worker(tile_id)
        if not worker.cfg.get("interactive", True):
            raise HTTPException(403, "tile is not interactive")
        try:
            if body.type == "click":
                worker.click(body.x, body.y, button=body.button, double=body.double)
            else:
                worker.scroll(body.x, body.y, body.delta)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] input 失败 {tile_id}: {exc}", file=sys.stderr)
            raise HTTPException(400, f"input failed: {exc}") from exc
        return {"ok": True}

    # ---- 流 ----

    @app.get("/stream/{tile_id}")
    def api_stream(tile_id: str, request: Request) -> StreamingResponse:
        worker = rt.worker(tile_id)
        return StreamingResponse(
            _mjpeg(worker),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "close",
                "X-Accel-Buffering": "no",
            },
        )

    app.mount("/static", StaticFiles(directory=ASSETS_DIR), name="static")
    return app
