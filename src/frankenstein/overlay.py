"""Snipaste 式区域选择。

交互流程（和 Snipaste 一致）：
    1. 在全屏遮罩上拖拽，拉出第一版选框
    2. 松开后**不立即截取**，选框进入可编辑状态：
       - 拖框内任意位置 → 移动
       - 拖 8 个手柄（四角 + 四边） → 缩放
       - 拖框外空白 → 重新拉框
    3. 双击选框内部、按 Enter、或点「完成」按钮 → 提交
       按 Esc 或点「取消」 → 放弃

遮罩显示的是按下快捷键那一刻的整屏截图；目标窗口保持高亮，其余压暗。
选区被限制在目标窗口内。
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
import urllib.request

from PIL import Image, ImageEnhance, ImageTk

_MIN_SIZE = 6          # 小于这个像素当作误触
_HANDLE = 7            # 手柄半径（画布像素）
_ACCENT = "#3b82f6"
_ACCENT_DIM = "#2563eb"
_TOOLBAR_H = 30


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

        self.win_rect = self._fetch_window_rect()

        self.sel: tuple[int, int, int, int] | None = None   # 画布坐标
        self.mode = "select"          # "select" | "adjust"
        self._drag: dict | None = None

        self._crop_photo: ImageTk.PhotoImage | None = None
        self._dyn_items: list[int] = []   # 每次重绘都清掉的临时图元

    # ---- 数据 ----

    def _fetch_window_rect(self) -> tuple[int, int, int, int] | None:
        try:
            for w in _http_json(f"{self.server}/api/windows"):
                if str(w.get("handle")) == self.handle:
                    r = w["rect"]
                    return int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
        except Exception:  # noqa: BLE001
            pass
        return None

    def _bounds(self) -> tuple[float, float, float, float]:
        """可拖拽范围：目标窗口（没有窗口信息就整屏）。"""
        if not self.win_rect:
            return 0.0, 0.0, float(self.W), float(self.H)
        wx, wy, ww, wh = self.win_rect
        return (
            max(0.0, wx - self.ox),
            max(0.0, wy - self.oy),
            min(float(self.W), wx - self.ox + ww),
            min(float(self.H), wy - self.oy + wh),
        )

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self._bounds()
        return max(x0, min(x1, x)), max(y0, min(y1, y))

    # ---- 绘制 ----

    def _clear_dynamic(self) -> None:
        for item in self._dyn_items:
            try:
                self.canvas.delete(item)
            except tk.TclError:
                pass
        self._dyn_items.clear()

    def _draw(self) -> None:
        self._clear_dynamic()
        sel = self.sel
        if not sel:
            self.canvas.itemconfigure(
                self.hint, text="拖拽框选区域   ·   Esc 取消"
            )
            return

        x1, y1, x2, y2 = [int(v) for v in sel]
        if x2 - x1 < 1 or y2 - y1 < 1:
            return

        # 选区显示原亮度
        crop = self.screen.crop((x1, y1, x2, y2))
        self._crop_photo = ImageTk.PhotoImage(crop)
        self._dyn_items.append(
            self.canvas.create_image(x1, y1, anchor="nw", image=self._crop_photo)
        )
        # 边框
        self._dyn_items.append(
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=_ACCENT, width=2)
        )

        if self.mode != "adjust":
            return

        # 8 个手柄
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        for hx, hy in (
            (x1, y1), (cx, y1), (x2, y1),
            (x1, cy), (x2, cy),
            (x1, y2), (cx, y2), (x2, y2),
        ):
            self._dyn_items.append(
                self.canvas.create_rectangle(
                    hx - _HANDLE, hy - _HANDLE, hx + _HANDLE, hy + _HANDLE,
                    fill="#ffffff", outline=_ACCENT, width=2,
                )
            )

        # 尺寸提示
        self._dyn_items.append(
            self.canvas.create_text(
                x1 + 4, max(10, y1 - 12),
                text=f"{x2 - x1} × {y2 - y1}",
                anchor="w", fill="#ffffff",
                font=("Sans", 10, "bold"),
            )
        )

        # 工具条：贴在选区右下角外侧
        tw, th = 132, _TOOLBAR_H
        tx = min(x2 - tw, self.W - tw - 4)
        ty = y2 + 8 if y2 + 8 + th < self.H else y2 - th - 8
        tx = max(4, tx)
        ty = max(4, ty)
        self._toolbar_rect = (tx, ty, tx + tw, ty + th)

        self._dyn_items.append(
            self.canvas.create_rectangle(
                tx, ty, tx + tw, ty + th, fill="#1f2937", outline="", width=0
            )
        )
        self._dyn_items.append(
            self.canvas.create_text(
                tx + tw * 0.28, ty + th / 2, text="✓ 完成",
                fill="#ffffff", font=("Sans", 10, "bold"),
            )
        )
        self._dyn_items.append(
            self.canvas.create_text(
                tx + tw * 0.72, ty + th / 2, text="✕ 取消",
                fill="#fca5a5", font=("Sans", 10),
            )
        )
        # 把提示压到底层图元之下
        self.canvas.tag_raise(self.hint)

    # ---- 命中测试 ----

    def _hit_handle(self, x: float, y: float) -> str:
        if not self.sel:
            return ""
        x1, y1, x2, y2 = self.sel
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        pts = {
            "nw": (x1, y1), "n": (cx, y1), "ne": (x2, y1),
            "w": (x1, cy), "e": (x2, cy),
            "sw": (x1, y2), "s": (cx, y2), "se": (x2, y2),
        }
        if not (x1 - _HANDLE * 2 <= x <= x2 + _HANDLE * 2 and
                y1 - _HANDLE * 2 <= y <= y2 + _HANDLE * 2):
            return ""
        for name, (hx, hy) in pts.items():
            if abs(x - hx) <= _HANDLE + 2 and abs(y - hy) <= _HANDLE + 2:
                return name
        return ""

    def _hit_toolbar(self, x: float, y: float) -> str:
        r = getattr(self, "_toolbar_rect", None)
        if not r:
            return ""
        tx, ty, tx2, ty2 = r
        if not (tx <= x <= tx2 and ty <= y <= ty2):
            return ""
        return "ok" if x < (tx + tx2) / 2 else "cancel"

    def _inside(self, x: float, y: float) -> bool:
        if not self.sel:
            return False
        x1, y1, x2, y2 = self.sel
        return x1 < x < x2 and y1 < y < y2

    # ---- 事件 ----

    def _press(self, evt: "tk.Event") -> None:
        x, y = self._clamp(evt.x, evt.y)

        if self.mode == "adjust" and self.sel:
            action = self._hit_toolbar(evt.x, evt.y)
            if action == "ok":
                self._commit()
                return
            if action == "cancel":
                self._cancel()
                return

            handle = self._hit_handle(evt.x, evt.y)
            if handle:
                self._drag = {"kind": "resize", "dir": handle, "base": self.sel}
                return
            if self._inside(evt.x, evt.y):
                self._drag = {"kind": "move", "start": (x, y), "base": self.sel}
                return
            # 点空白处 → 重新拉框
            self.mode = "select"
            self.sel = None

        self._drag = {"kind": "new", "start": (x, y)}
        self.sel = (int(x), int(y), int(x), int(y))
        self._draw()

    def _motion(self, evt: "tk.Event") -> None:
        d = self._drag
        if not d:
            return
        x, y = self._clamp(evt.x, evt.y)

        if d["kind"] == "new":
            sx, sy = d["start"]
            self.sel = (
                int(min(sx, x)), int(min(sy, y)),
                int(max(sx, x)), int(max(sy, y)),
            )
        elif d["kind"] == "move":
            dx = x - d["start"][0]
            dy = y - d["start"][1]
            bx1, by1, bx2, by2 = d["base"]
            x0, y0, x1, y1 = self._bounds()
            dx = max(x0 - bx1, min(x1 - bx2, dx))
            dy = max(y0 - by1, min(y1 - by2, dy))
            self.sel = (int(bx1 + dx), int(by1 + dy), int(bx2 + dx), int(by2 + dy))
        else:
            bx1, by1, bx2, by2 = d["base"]
            dir_ = d["dir"]
            nx1, ny1, nx2, ny2 = bx1, by1, bx2, by2
            if "w" in dir_:
                nx1 = min(x, nx2 - _MIN_SIZE)
            if "e" in dir_:
                nx2 = max(x, nx1 + _MIN_SIZE)
            if "n" in dir_:
                ny1 = min(y, ny2 - _MIN_SIZE)
            if "s" in dir_:
                ny2 = max(y, ny1 + _MIN_SIZE)
            self.sel = (int(nx1), int(ny1), int(nx2), int(ny2))
        self._draw()

    def _release(self, evt: "tk.Event") -> None:
        d = self._drag
        self._drag = None
        if not d:
            return
        if d["kind"] == "new":
            if not self.sel or (self.sel[2] - self.sel[0]) < _MIN_SIZE or \
               (self.sel[3] - self.sel[1]) < _MIN_SIZE:
                self.sel = None
                self.mode = "select"
            else:
                self.mode = "adjust"
        self._draw()

    def _double(self, evt: "tk.Event") -> None:
        if self.sel and self._inside(evt.x, evt.y):
            self._commit()

    def _key(self, evt: "tk.Event") -> str:
        if evt.keysym == "Escape":
            self._cancel()
            return "break"
        if evt.keysym in ("Return", "KP_Enter") and self.sel:
            self._commit()
            return "break"
        return ""

    def _on_right(self, _evt: "tk.Event") -> str:
        """右键 = 取消（Snipaste 的习惯）。"""
        self._cancel()
        return "break"

    # ---- 提交 / 取消 ----

    def _commit(self) -> None:
        if not self.sel:
            return
        x1, y1, x2, y2 = self.sel
        w, h = x2 - x1, y2 - y1
        if w < _MIN_SIZE or h < _MIN_SIZE:
            self._cancel()
            return
        try:
            _http_json(
                f"{self.server}/api/capture-region",
                {"handle": self.handle, "x": x1 + self.ox, "y": y1 + self.oy,
                 "w": w, "h": h},
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[!] 创建贴图失败: {exc}", file=sys.stderr)
        self.root.destroy()

    def _cancel(self) -> None:
        self.root.destroy()

    # ---- 生命周期 ----

    def run(self) -> int:
        self._build_ui()
        self.root.mainloop()
        return 0

    def _build_ui(self) -> None:
        """构建遮罩窗口（单独拆出来，方便测试里不走 mainloop 直接驱动）。"""
        self.root = tk.Tk()
        self.root.title("选择区域")
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
                    box[0], box[1], box[2], box[3], outline=_ACCENT, width=1, dash=(6, 4)
                )

        self.hint = self.canvas.create_text(
            self.W // 2, 46,
            fill="#ffffff",
            font=("Sans", 13),
            text="拖拽框选区域   ·   Esc 取消",
        )

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._double)

        # 右键取消：不依赖键盘焦点，任何时候都能退
        for w in (self.root, self.canvas):
            w.bind("<ButtonPress-3>", self._on_right)
            w.bind("<Button-3>", self._on_right)
            w.bind("<Escape>", self._key)
        self.root.bind("<Return>", self._key)
        self.root.bind("<KP_Enter>", self._key)
        # 再加一层应用级绑定：即使焦点落在子控件上，Esc 也能退
        self.root.bind_all("<Escape>", self._key)

        # 遮罩是模态的：grab 之后键盘事件必定送到这里，Esc 才稳定有效。
        # overrideredirect 窗口默认可能拿不到输入焦点。
        self.root.update_idletasks()
        self.canvas.focus_set()
        self.root.focus_force()
        try:
            self.root.grab_set()
        except tk.TclError:
            pass
        self.root.lift()


def run_selector(server: str, handle: str) -> int:
    try:
        return RegionSelector(server, handle).run()
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 区域选择启动失败：{exc}", file=sys.stderr)
        return 1
