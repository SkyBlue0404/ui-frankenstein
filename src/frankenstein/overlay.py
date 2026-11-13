"""Snipaste 式区域选择：全屏遮罩 + 拖拽框选，选完直接贴到桌面。

    python run.py --select --handle <窗口句柄> --server http://127.0.0.1:8777

遮罩显示的是**按下快捷键那一刻的整屏截图**，目标窗口保持高亮，其余压暗。
选区被限制在目标窗口内，松手后把屏幕坐标换算成窗口内归一化坐标交给服务端。
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
import urllib.request

from PIL import Image, ImageEnhance, ImageTk

_MIN_SIZE = 6          # 小于这个像素当作误触
_MIN_SPAN = 0.004      # 归一化后的最小尺寸


def _http_json(url: str, body: dict | None = None, timeout: float = 6.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _grab_screen() -> tuple[Image.Image, dict]:
    import mss

    with mss.mss() as sct:
        mon = sct.monitors[0]  # 所有屏幕的并集
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img, mon


class RegionSelector:
    def __init__(self, server: str, handle: str) -> None:
        self.server = server.rstrip("/")
        self.handle = str(handle)
        self.screen, self.mon = _grab_screen()
        self.ox = int(self.mon["left"])
        self.oy = int(self.mon["top"])
        self.W, self.H = self.screen.size

        # 目标窗口在屏幕上的矩形（用来高亮 + 限制选区）
        self.win_rect = self._fetch_window_rect()

        self.start: tuple[float, float] | None = None
        self.sel: tuple[int, int, int, int] | None = None
        self._crop_photo: ImageTk.PhotoImage | None = None
        self._crop_item: int | None = None
        self._box_item: int | None = None

    # ---- 数据 ----

    def _fetch_window_rect(self) -> tuple[int, int, int, int] | None:
        """服务端窗口列表 -> (x, y, w, h) 屏幕坐标。"""
        try:
            windows = _http_json(f"{self.server}/api/windows")
            for w in windows:
                if str(w.get("handle")) == self.handle:
                    r = w["rect"]
                    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
        except Exception:  # noqa: BLE001
            pass
        return None

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        """把画布坐标夹到目标窗口内（没有窗口信息就不限制）。"""
        if not self.win_rect:
            return x, y
        wx, wy, ww, wh = self.win_rect
        x0, y0 = wx - self.ox, wy - self.oy
        return (
            max(x0, min(x0 + ww, x)),
            max(y0, min(y0 + wh, y)),
        )

    # ---- 绘制 ----

    def _draw(self) -> None:
        if self._crop_item is not None:
            self.canvas.delete(self._crop_item)
            self._crop_item = None
        if self._box_item is not None:
            self.canvas.delete(self._box_item)
            self._box_item = None
        if not self.sel:
            return

        x1, y1, x2, y2 = self.sel
        if x2 - x1 < 1 or y2 - y1 < 1:
            return

        # 选区显示原亮度
        crop = self.screen.crop((x1, y1, x2, y2))
        self._crop_photo = ImageTk.PhotoImage(crop)
        self._crop_item = self.canvas.create_image(x1, y1, anchor="nw", image=self._crop_photo)
        self._box_item = self.canvas.create_rectangle(
            x1, y1, x2, y2, outline="#4c8dff", width=2
        )
        self.canvas.tag_raise(self.hint)
        self.canvas.itemconfigure(
            self.hint, text=f"{x2 - x1} × {y2 - y1} px   ·   松开贴到桌面，Esc 取消"
        )

    # ---- 事件 ----

    def _press(self, evt: "tk.Event") -> None:
        self.start = self._clamp(evt.x, evt.y)
        self.sel = (int(self.start[0]), int(self.start[1]), int(self.start[0]), int(self.start[1]))
        self._draw()

    def _motion(self, evt: "tk.Event") -> None:
        if not self.start:
            return
        x, y = self._clamp(evt.x, evt.y)
        self.sel = (
            int(min(self.start[0], x)), int(min(self.start[1], y)),
            int(max(self.start[0], x)), int(max(self.start[1], y)),
        )
        self._draw()

    def _release(self, evt: "tk.Event") -> None:
        if not self.sel:
            self._cancel()
            return
        x1, y1, x2, y2 = self.sel
        w, h = x2 - x1, y2 - y1
        if w < _MIN_SIZE or h < _MIN_SIZE:
            self._cancel()
            return

        body = {
            "handle": self.handle,
            "x": x1 + self.ox,
            "y": y1 + self.oy,
            "w": w,
            "h": h,
        }
        try:
            _http_json(f"{self.server}/api/capture-region", body)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] 创建贴图失败: {exc}", file=sys.stderr)
        self.root.destroy()

    def _cancel(self) -> None:
        self.root.destroy()

    # ---- 生命周期 ----

    def run(self) -> int:
        self.root = tk.Tk()
        self.root.title("选择区域")
        # 用 overrideredirect + 显式 geometry 覆盖**所有显示器**（-fullscreen
        # 只会铺满当前那一块屏）。无边框、置顶，靠定时 lift 保持最前。
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.W}x{self.H}+{self.ox}+{self.oy}")
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.root, width=self.W, height=self.H,
            highlightthickness=0, bd=0, cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        # 整屏压暗
        dim = ImageEnhance.Brightness(self.screen).enhance(0.38)
        self._dim_photo = ImageTk.PhotoImage(dim)
        self.canvas.create_image(0, 0, anchor="nw", image=self._dim_photo)

        # 目标窗口保持原亮度
        if self.win_rect:
            wx, wy, ww, wh = self.win_rect
            x0, y0 = wx - self.ox, wy - self.oy
            box = (max(0, x0), max(0, y0), min(self.W, x0 + ww), min(self.H, y0 + wh))
            if box[2] > box[0] and box[3] > box[1]:
                self._win_photo = ImageTk.PhotoImage(self.screen.crop(box))
                self.canvas.create_image(box[0], box[1], anchor="nw", image=self._win_photo)
                self.canvas.create_rectangle(
                    box[0], box[1], box[2], box[3], outline="#4c8dff", width=1, dash=(6, 4)
                )

        self.hint = self.canvas.create_text(
            self.W // 2, 46,
            fill="#ffffff",
            font=("Sans", 13),
            text="拖拽框选要贴到桌面的区域   ·   Esc 取消",
        )

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Escape>", lambda e: self._cancel())

        self.root.update_idletasks()
        self.root.focus_force()
        self.root.lift()
        self.root.mainloop()
        return 0


def run_selector(server: str, handle: str) -> int:
    try:
        return RegionSelector(server, handle).run()
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 区域选择启动失败：{exc}", file=sys.stderr)
        return 1
