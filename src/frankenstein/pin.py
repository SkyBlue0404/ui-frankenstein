"""Snipaste 风格的「贴图」窗口。

把一个 tile 的实时画面钉在桌面最上层：无边框、可拖动、点击直接回传到原窗口。
因为它是**实时**画面而不是静态截图，原窗口里按钮的状态变化会同步显示出来。

    python run.py --pin <tile-id>              # 贴到桌面
    python run.py --pin <tile-id> --scale 1.5  # 放大 1.5 倍
"""

from __future__ import annotations

import io
import queue
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request

from PIL import Image, ImageTk

_FPS = 15  # 贴图窗口的刷新率
_CLICK_TOLERANCE = 4  # 拖动超过这个像素就算「拖拽」而不是「点击」


# ---------------------------------------------------------------------------
# 后台拉帧
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


class _Poller(threading.Thread):
    """后台线程按固定帧率拉取 JPEG，丢进队列，主线程消费。"""

    def __init__(self, url: str, out: "queue.Queue[bytes]") -> None:
        super().__init__(daemon=True, name="pin-poller")
        self.url = url
        self.out = out
        self._stop = threading.Event()
        self.error: str | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        interval = 1.0 / _FPS
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                data = _http_get(self.url)
                if data:
                    # 只保留最新一帧，避免主线程处理不过来时堆积
                    while not self.out.empty():
                        try:
                            self.out.get_nowait()
                        except queue.Empty:
                            break
                    self.out.put(data)
                    self.error = None
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(max(0.005, interval - (time.perf_counter() - t0)))


# ---------------------------------------------------------------------------
# 贴图窗口
# ---------------------------------------------------------------------------


class PinWindow:
    def __init__(self, server: str, tile_id: str, scale: float = 1.0,
                 x: int | None = None, y: int | None = None,
                 own_tile: bool = False) -> None:
        self.server = server.rstrip("/")
        self.tile_id = tile_id
        self.scale = max(0.2, min(6.0, float(scale)))
        # 模式 A 快捷键截取的「瞬时 tile」由贴图窗口自己持有，关窗即回收
        self.own_tile = own_tile

        self._frames: "queue.Queue[bytes]" = queue.Queue()
        self._poller = _Poller(
            f"{self.server}/api/tiles/{urllib.parse.quote(tile_id)}/frame.jpg", self._frames
        )

        self._photo: ImageTk.PhotoImage | None = None
        self._img_size = (0, 0)  # 原图尺寸（裁剪图像素）
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._dragging = False
        self._closed = False
        self._menu_open = False
        self._popup: "_PopupMenu | None" = None

        first = self._wait_first_frame()
        self._img_size = first.size

        self.root = tk.Tk()
        self.root.title(f"Pin · {tile_id}")
        self.root.overrideredirect(True)  # 无边框
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        w = max(40, int(self._img_size[0] * self.scale))
        h = max(20, int(self._img_size[1] * self.scale))
        px = x if x is not None else 120
        py = y if y is not None else 120
        self.root.geometry(f"{w}x{h}+{px}+{py}")

        # 1px 描边 + 阴影感
        self.frame = tk.Frame(self.root, bg="#4c8dff", bd=0)
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.label = tk.Label(self.frame, bd=0, bg="#000000", cursor="hand2")
        self.label.pack(fill="both", expand=True)

        self._bind_events()
        self._build_menu()
        self._poller.start()

        # 等 pack/geometry 生效，否则 winfo_width() 还是 1，首帧会被按 1x1 处理
        self.root.update_idletasks()
        self._paint(first)
        self.root.after(20, self._tick)
        self.root.after(1200, self._keep_on_top)

    # ---- 启动 ----

    def _wait_first_frame(self, timeout: float = 6.0) -> Image.Image:
        deadline = time.time() + timeout
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                data = _http_get(
                    f"{self.server}/api/tiles/{urllib.parse.quote(self.tile_id)}/frame.jpg",
                    timeout=2.0,
                )
                return Image.open(io.BytesIO(data)).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.25)
        raise RuntimeError(f"拿不到 {self.tile_id} 的画面：{last_err}")

    # ---- 渲染 ----

    def _paint(self, img: Image.Image) -> None:
        cw = max(1, self.label.winfo_width())
        ch = max(1, self.label.winfo_height())
        disp = self._fit(img, cw, ch)
        self._photo = ImageTk.PhotoImage(disp)
        self.label.configure(image=self._photo)

    def _fit(self, img: Image.Image, box_w: int, box_h: int) -> Image.Image:
        """等比缩放并居中（letterbox），保证不拉伸。"""
        if box_w <= 1 or box_h <= 1:
            return img
        ratio = min(box_w / img.width, box_h / img.height)
        if abs(ratio - 1.0) < 0.01:
            return img
        return img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))), Image.LANCZOS)

    def _tick(self) -> None:
        if self._closed:
            return
        latest: bytes | None = None
        try:
            while True:
                latest = self._frames.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            try:
                img = Image.open(io.BytesIO(latest)).convert("RGB")
                if img.size != self._img_size:
                    self._img_size = img.size
                    self._resize_to_content()
                self._paint(img)
            except Exception:  # noqa: BLE001
                pass
        self.root.after(1000 // _FPS, self._tick)

    def _resize_to_content(self) -> None:
        """原窗口尺寸变了，贴图也跟着变，但不改变左上角位置。"""
        try:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(
                f"{max(40, int(self._img_size[0] * self.scale))}x"
                f"{max(20, int(self._img_size[1] * self.scale))}+{x}+{y}"
            )
        except tk.TclError:
            pass

    def _keep_on_top(self) -> None:
        if self._closed:
            return
        # 右键菜单弹出期间不要 lift：贴图自己是 topmost，
        # 一提升就会盖住菜单，导致点菜单项落到投影上（穿透）。
        if not self._menu_open:
            try:
                self.root.lift()
            except tk.TclError:
                return
        self.root.after(1500, self._keep_on_top)

    # ---- 交互 ----

    def _bind_events(self) -> None:
        for w in (self.root, self.frame, self.label):
            w.bind("<ButtonPress-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_motion)
            w.bind("<ButtonRelease-1>", self._on_release)
            w.bind("<ButtonPress-3>", self._on_menu)
            w.bind("<MouseWheel>", self._on_wheel)       # Windows / macOS
            w.bind("<Button-4>", lambda e: self._scroll(e, +1))  # X11 上滚
            w.bind("<Button-5>", lambda e: self._scroll(e, -1))  # X11 下滚
            w.bind("<Enter>", lambda e: self.frame.configure(bg="#7aa8ff"))
            w.bind("<Leave>", lambda e: self.frame.configure(bg="#4c8dff"))

    def _on_press(self, evt: "tk.Event") -> None:
        if self._menu_open:  # 菜单弹出时，落在贴图上的事件一律忽略
            return
        self._drag_origin = (evt.x_root, evt.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._dragging = False

    def _on_motion(self, evt: "tk.Event") -> None:
        if not self._drag_origin:
            return
        sx, sy, wx, wy = self._drag_origin
        dx, dy = evt.x_root - sx, evt.y_root - sy
        if abs(dx) > _CLICK_TOLERANCE or abs(dy) > _CLICK_TOLERANCE:
            self._dragging = True
        if self._dragging:
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def _on_release(self, evt: "tk.Event") -> None:
        if self._menu_open:  # 右键菜单刚关掉时可能漏过来的事件，不要回传
            self._drag_origin = None
            self._dragging = False
            return
        was_drag = self._dragging
        self._drag_origin = None
        self._dragging = False
        if was_drag:
            return  # 拖拽结束，不触发点击
        pt = self._to_crop(evt)
        if pt:
            self._post_input({"type": "click", "x": pt[0], "y": pt[1], "button": 1})

    def _on_wheel(self, evt: "tk.Event") -> None:
        # Windows 的 delta 是 120 的倍数
        self._scroll(evt, 1 if evt.delta > 0 else -1)

    def _scroll(self, evt: "tk.Event", direction: int) -> None:
        pt = self._to_crop(evt)
        if pt:
            self._post_input({"type": "scroll", "x": pt[0], "y": pt[1], "delta": direction})

    def _to_crop(self, evt: "tk.Event") -> tuple[float, float] | None:
        """把贴图窗口内的坐标换算成裁剪图像素坐标（含 letterbox 去边）。

        统一用 root 坐标减去 label 的屏幕原点，这样不管事件落在 root / frame /
        label 哪个控件上，结果都一样（1px 描边不会造成偏移）。
        """
        cw = self.label.winfo_width()
        ch = self.label.winfo_height()
        iw, ih = self._img_size
        if cw <= 1 or ch <= 1 or iw <= 0 or ih <= 0:
            return None
        lx = evt.x_root - self.label.winfo_rootx()
        ly = evt.y_root - self.label.winfo_rooty()
        ratio = min(cw / iw, ch / ih)
        dw, dh = iw * ratio, ih * ratio
        ox, oy = (cw - dw) / 2, (ch - dh) / 2
        x = (lx - ox) / ratio
        y = (ly - oy) / ratio
        if x < 0 or y < 0 or x > iw or y > ih:
            return None
        return max(0.0, min(iw, x)), max(0.0, min(ih, y))

    def _post_input(self, body: dict) -> None:
        def worker() -> None:
            try:
                req = urllib.request.Request(
                    f"{self.server}/api/tiles/{urllib.parse.quote(self.tile_id)}/input",
                    data=__import__("json").dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=worker, daemon=True).start()

    # ---- 右键菜单 ----

    def _build_menu(self) -> None:
        """菜单项只描述数据，实际弹出用的是自绘的 _PopupMenu（见下）。"""
        self._menu_items = [
            ("放大", lambda: self._zoom(1.25)),
            ("缩小", lambda: self._zoom(0.8)),
            ("原始大小", lambda: self._set_scale(1.0)),
            None,
            ("不透明度 100%", lambda: self._opacity(100)),
            ("不透明度 75%", lambda: self._opacity(75)),
            ("不透明度 50%", lambda: self._opacity(50)),
            None,
            ("关闭贴图", self.close),
        ]

    def _on_menu(self, evt: "tk.Event") -> None:
        if self._menu_open:
            return
        self._menu_open = True
        try:
            self._popup = _PopupMenu(
                self.root, self._menu_items, on_close=self._menu_closed
            )
            self._popup.post(evt.x_root, evt.y_root)
        except Exception:  # noqa: BLE001
            self._menu_open = False
            raise

    def _menu_closed(self) -> None:
        self._menu_open = False
        self._popup = None

    def _zoom(self, factor: float) -> None:
        self._set_scale(self.scale * factor)

    def _set_scale(self, value: float) -> None:
        self.scale = max(0.2, min(6.0, value))
        self._resize_to_content()

    def _opacity(self, pct: int) -> None:
        try:
            self.root.attributes("-alpha", pct / 100.0)
        except tk.TclError:
            pass

    # ---- 生命周期 ----

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._poller.stop()
        if self.own_tile:
            threading.Thread(target=self._release_tile, daemon=True).start()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _release_tile(self) -> None:
        """回收服务端为这张贴图创建的瞬时 tile。"""
        try:
            req = urllib.request.Request(
                f"{self.server}/api/tiles/{urllib.parse.quote(self.tile_id)}",
                method="DELETE",
            )
            urllib.request.urlopen(req, timeout=2).read()
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._poller.stop()


# ---------------------------------------------------------------------------
# 自绘右键菜单
# ---------------------------------------------------------------------------


class _PopupMenu:
    """极简卡片式右键菜单。

    不用 tk.Menu，因为原生菜单跟随系统主题、边框厚重，也压不住 topmost 的贴图。
    自绘一个无边框 Toplevel：白底 + 细边 + 悬停淡蓝，视觉上干净很多。
    """

    _BG = "#ffffff"
    _BORDER = "#e4e9f2"
    _TEXT = "#1f2430"
    _HOVER = "#f0f5ff"
    _DANGER = "#d94a5b"
    _SEP = "#eef2f7"

    def __init__(self, master: "tk.Misc", items: list, on_close) -> None:
        self._on_close = on_close
        self._closed = False

        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        try:
            self.top.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.top.configure(bg=self._BORDER)  # 1px 边框

        body = tk.Frame(self.top, bg=self._BG)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        for item in items:
            if item is None:
                sep = tk.Frame(body, bg=self._SEP, height=1)
                sep.pack(fill="x", padx=10, pady=4)
                continue

            label, cmd = item
            danger = label.startswith("关闭")
            row = tk.Label(
                body,
                text=label,
                anchor="w",
                bg=self._BG,
                fg=self._DANGER if danger else self._TEXT,
                font=("Sans", 10),
                padx=16,
                pady=6,
                cursor="hand2",
                bd=0,
                highlightthickness=0,
            )
            row.pack(fill="x")
            row.bind("<Enter>", lambda e, r=row: r.configure(bg=self._HOVER))
            row.bind("<Leave>", lambda e, r=row: r.configure(bg=self._BG))
            row.bind("<ButtonRelease-1>", lambda e, c=cmd: self._run(c))

        self.top.bind("<ButtonRelease-1>", self._maybe_close)
        self.top.bind("<Escape>", lambda e: self.close())

    # ---- 行为 ----

    def _run(self, cmd) -> None:
        self.close()
        try:
            cmd()
        except Exception:  # noqa: BLE001
            pass

    def _maybe_close(self, evt: "tk.Event") -> None:
        # grab 之后窗口外的点击也会送到这里，坐标落在 [0,w) 之外
        w, h = self.top.winfo_width(), self.top.winfo_height()
        if evt.x < 0 or evt.y < 0 or evt.x >= w or evt.y >= h:
            self.close()

    def post(self, x: int, y: int) -> None:
        self.top.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        x = max(6, min(int(x), sw - w - 6))
        y = max(6, min(int(y), sh - h - 6))
        self.top.geometry(f"+{x}+{y}")
        self.top.deiconify()
        self.top.lift()
        try:
            self.top.grab_set()  # 捕获外部点击，好让点空白处能关掉
        except tk.TclError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        self._on_close()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_pin(server: str, tile_id: str, scale: float = 1.0,
            x: int | None = None, y: int | None = None,
            own_tile: bool = False) -> int:
    try:
        app = PinWindow(server, tile_id, scale=scale, x=x, y=y, own_tile=own_tile)
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 贴图启动失败：{exc}")
        return 1
    app.run()
    return 0
