from __future__ import annotations

import io
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .capture import TileWorker
from .config import Config
from .platform import get_backend

STATIC_DIR = Path(__file__).parent / "static"
_BOUNDARY = b"--frame\r\n"


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


# ---------------------------------------------------------------------------
# 运行时状态
# ---------------------------------------------------------------------------


class Runtime:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.backend = get_backend()
        self.workers: dict[str, TileWorker] = {}
        self.lock = threading.RLock()

    def start(self) -> None:
        with self.lock:
            self._stop_all()
            for tile in self.cfg.tiles:
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
            wanted = {t["id"]: t for t in self.cfg.tiles}
            for tid, worker in list(self.workers.items()):
                if tid not in wanted or worker.cfg != wanted[tid]:
                    worker.stop()
                    del self.workers[tid]
            for tid, tile in wanted.items():
                if tid not in self.workers:
                    worker = TileWorker(tile, self.backend)
                    worker.start()
                    self.workers[tid] = worker

    def worker(self, tile_id: str) -> TileWorker:
        with self.lock:
            w = self.workers.get(tile_id)
        if w is None:
            raise HTTPException(404, f"unknown tile: {tile_id}")
        return w

    def close(self) -> None:
        with self.lock:
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

    app = FastAPI(title="Window Dashboard", version="0.1.0")
    app.state.runtime = rt

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover
        rt.close()

    # ---- 页面 ----

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/setup", include_in_schema=False)
    def setup_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "setup.html")

    # ---- 状态 ----

    @app.get("/api/state")
    def api_state() -> dict[str, Any]:
        with rt.lock:
            workers = list(rt.workers.values())
        return {
            "platform": rt.backend.name,
            "layout": cfg.layout,
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
                str(Path(__file__).resolve().parent.parent / "run.py"),
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

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
