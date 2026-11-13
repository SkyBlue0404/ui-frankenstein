from __future__ import annotations

import io
import re
import threading
import time
from collections import deque
from typing import Any

from PIL import Image

from .platform.base import PlatformBackend

# 窗口句柄的复用 TTL：每次抓帧都重新匹配会明显增加延迟，
# 但句柄失效后必须尽快重新解析，2 秒是个折中。
_HANDLE_TTL = 2.0


class TileWorker(threading.Thread):
    """一个 tile 对应一个后台抓帧线程。

    线程持续把「窗口 → 裁剪区域 → JPEG」推送到共享槽位，
    HTTP 端只需要读最新帧，天然实现了背压（慢客户端不会拖垮抓帧）。
    """

    def __init__(self, cfg: dict[str, Any], backend: PlatformBackend) -> None:
        super().__init__(daemon=True, name=f"tile-{cfg['id']}")
        self.cfg = cfg
        self.backend = backend

        self._stop = threading.Event()
        self._cond = threading.Condition()

        self._jpeg: bytes | None = None
        self._seq = 0
        self._crop_size: tuple[int, int] = (0, 0)
        self._win_size: tuple[int, int] = (0, 0)
        self._error: str | None = None
        self._handle: str | None = None

        self._handle_ts = 0.0
        self._frame_ts: deque[float] = deque(maxlen=32)
        self._fps = 0.0

    # ---- 生命周期 ----

    @property
    def id(self) -> str:
        return self.cfg["id"]

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                crop = self._grab()
                buf = io.BytesIO()
                crop.save(buf, "JPEG", quality=int(self.cfg.get("quality", 80)))
                with self._cond:
                    self._jpeg = buf.getvalue()
                    self._seq += 1
                    self._error = None
                    self._cond.notify_all()
            except Exception as exc:  # noqa: BLE001 - 任何异常都要显示在页面上
                with self._cond:
                    self._error = f"{type(exc).__name__}: {exc}"
                    self._cond.notify_all()

            now = time.perf_counter()
            self._frame_ts.append(now)
            if len(self._frame_ts) > 1:
                span = self._frame_ts[-1] - self._frame_ts[0]
                self._fps = (len(self._frame_ts) - 1) / span if span > 0 else 0.0

            interval = 1.0 / max(0.5, float(self.cfg.get("fps", 8)))
            self._stop.wait(max(0.0, interval - (now - t0)))

    # ---- 抓取 ----

    def _grab(self) -> Image.Image:
        handle = self._resolve_handle()
        if handle is None:
            raise RuntimeError(self._not_found_message())

        img = self.backend.grab_window(handle, mode=self.cfg.get("capture", "window"))
        W, H = img.size
        if W <= 0 or H <= 0:
            raise RuntimeError("captured window is empty")

        region = self.cfg["region"]
        x0 = int(round(region["x"] * W))
        y0 = int(round(region["y"] * H))
        x1 = int(round((region["x"] + region["w"]) * W))
        y1 = int(round((region["y"] + region["h"]) * H))
        x0 = max(0, min(W - 1, x0))
        y0 = max(0, min(H - 1, y0))
        x1 = max(x0 + 1, min(W, x1))
        y1 = max(y0 + 1, min(H, y1))

        crop = img.crop((x0, y0, x1, y1))
        with self._cond:
            self._win_size = (W, H)
            self._crop_size = crop.size
        return crop

    def _not_found_message(self) -> str:
        win = self.cfg["window"]
        bits = []
        if win.get("handle"):
            bits.append(f"handle={win['handle']}")
        if win.get("title_regex"):
            bits.append(f"title≈/{win['title_regex']}/")
        if win.get("app_regex"):
            bits.append(f"app≈/{win['app_regex']}/")
        return "window not found (" + (", ".join(bits) or "no matcher") + ")"

    def _resolve_handle(self) -> str | None:
        now = time.monotonic()
        if self._handle and now - self._handle_ts < _HANDLE_TTL:
            return self._handle

        windows = self.backend.list_windows()
        win = self.cfg["window"]

        # 1) 显式 handle 优先（但要求它还在）
        explicit = win.get("handle")
        if explicit:
            for w in windows:
                if w.handle == explicit:
                    self._handle, self._handle_ts = w.handle, now
                    return w.handle
            self._handle = None

        # 2) 正则匹配
        title_re = re.compile(win["title_regex"]) if win.get("title_regex") else None
        app_re = re.compile(win["app_regex"]) if win.get("app_regex") else None

        matches = []
        for w in windows:
            if title_re and not title_re.search(w.title):
                continue
            if app_re and not app_re.search(w.app or ""):
                continue
            matches.append(w)

        idx = int(win.get("index") or 0)
        if idx < 0:
            idx += len(matches)
        if 0 <= idx < len(matches):
            self._handle, self._handle_ts = matches[idx].handle, now
            return self._handle

        self._handle = None
        return None

    # ---- 供 HTTP 层读取 ----

    def wait_frame(self, last_seq: int, timeout: float = 5.0) -> tuple[int, bytes | None]:
        with self._cond:
            if self._seq == last_seq and not self._stop.is_set():
                self._cond.wait(timeout)
            return self._seq, self._jpeg

    def latest_jpeg(self) -> bytes | None:
        """取当前最新一帧（贴图窗口轮询用）。"""
        with self._cond:
            return self._jpeg

    def status(self) -> dict[str, Any]:
        with self._cond:
            return {
                "id": self.id,
                "title": self.cfg.get("title", self.id),
                "ok": self._error is None and self._jpeg is not None,
                "error": self._error,
                "fps": round(self._fps, 1),
                "crop_size": list(self._crop_size),
                "window_size": list(self._win_size),
                "handle": self._handle,
                "interactive": bool(self.cfg.get("interactive", True)),
                "canvas": self.cfg.get("canvas"),
            }

    def latest_size(self) -> tuple[tuple[int, int], tuple[int, int]]:
        with self._cond:
            return self._win_size, self._crop_size

    # ---- 输入 ----

    def click(self, cx: float, cy: float, button: int = 1, double: bool = False) -> None:
        """cx, cy 是裁剪图上的像素坐标。"""
        handle = self._handle or self._resolve_handle()
        if handle is None:
            raise RuntimeError(self._not_found_message())
        (W, H), (CW, CH) = self.latest_size()
        if CW <= 0 or CH <= 0:
            raise RuntimeError("no frame yet")
        region = self.cfg["region"]
        px = int(round(region["x"] * W + cx))
        py = int(round(region["y"] * H + cy))
        px = max(0, min(W - 1, px))
        py = max(0, min(H - 1, py))
        self.backend.click(handle, px, py, button=button, double=double)

    def scroll(self, cx: float, cy: float, delta: int) -> None:
        handle = self._handle or self._resolve_handle()
        if handle is None:
            raise RuntimeError(self._not_found_message())
        (W, H), (CW, CH) = self.latest_size()
        if CW <= 0 or CH <= 0:
            raise RuntimeError("no frame yet")
        region = self.cfg["region"]
        px = int(round(region["x"] * W + cx))
        py = int(round(region["y"] * H + cy))
        self.backend.scroll(handle, max(0, min(W - 1, px)), max(0, min(H - 1, py)), delta)
