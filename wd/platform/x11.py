from __future__ import annotations

import threading
import time
from typing import Any

from PIL import Image

from ..models import Rect, WindowInfo
from .base import PlatformBackend

# 这些 import 只在 Linux 上有意义，放在模块级由 platform/__init__ 懒加载
from Xlib import X, display  # type: ignore
from Xlib.ext import xtest  # type: ignore
from Xlib.protocol import event  # type: ignore

_BUTTON_MAP = {1: 1, 2: 2, 3: 3}  # our button -> X button

# XTest 注入的是「屏幕坐标」，点击永远落在最上层的窗口上。
# 所以目标窗口被遮挡时必须先把它抬到最前，点完再还原焦点，
# 否则点击会打到浏览器/编辑器上（而不是被拼接的工具窗口）。
_ACTIVATE_BEFORE_CLICK = True
_RESTORE_FOCUS_AFTER_CLICK = True


class X11Backend(PlatformBackend):
    """X11 (Xorg) 后端。

    注意：Wayland 会话下 XGetImage 拿不到真窗口内容，需要改用
    `capture: "screen"` 模式（要求窗口可见），或切到 Xorg 会话。
    """

    name = "x11"

    def __init__(self) -> None:
        self._dpy = display.Display()
        self._root = self._dpy.screen().root
        self._lock = threading.RLock()  # Xlib 连接不是线程安全的
        self._local = threading.local()  # mss 实例不能跨线程共享
        self._atom_net_name = self._dpy.intern_atom("_NET_WM_NAME")
        self._atom_utf8 = self._dpy.intern_atom("UTF8_STRING")
        self._atom_client_list = self._dpy.intern_atom("_NET_CLIENT_LIST")
        self._atom_wm_class = self._dpy.intern_atom("WM_CLASS")
        self._atom_pid = self._dpy.intern_atom("_NET_WM_PID")
        self._atom_active = self._dpy.intern_atom("_NET_ACTIVE_WINDOW")

    # ---- 内部 ----

    def _mss(self) -> Any:
        inst = getattr(self._local, "sct", None)
        if inst is None:
            import mss  # 懒加载，避免无 DISPLAY 时模块级报错

            inst = mss.mss()
            self._local.sct = inst
        return inst

    def _client_windows(self) -> list[Any]:
        """优先用 EWMH 的 _NET_CLIENT_LIST，没有就退回 root 子窗口递归。"""
        prop = self._root.get_full_property(self._atom_client_list, X.AnyPropertyType)
        wins = []
        if prop is not None and prop.value is not None:
            for wid in prop.value:
                try:
                    wins.append(self._dpy.create_resource_object("window", int(wid)))
                except Exception:
                    continue
        if wins:
            return wins

        # 兜底：root 的直接子窗口里找带名字的（reparenting WM 下可能只是 frame）
        out = []
        stack = list(self._root.query_tree().children)
        seen = 0
        while stack and seen < 2000:
            w = stack.pop()
            seen += 1
            try:
                if w.get_wm_name():
                    out.append(w)
                    continue
                stack.extend(w.query_tree().children)
            except Exception:
                continue
        return out

    def _title(self, w: Any) -> str:
        try:
            prop = w.get_full_property(self._atom_net_name, self._atom_utf8)
            if prop is not None and prop.value:
                v = prop.value
                return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        except Exception:
            pass
        try:
            return w.get_wm_name() or ""
        except Exception:
            return ""

    def _app(self, w: Any) -> str:
        # _NET_WM_PID -> 进程名；失败就用 WM_CLASS 的 class 段
        try:
            prop = w.get_full_property(self._atom_pid, X.AnyPropertyType)
            if prop is not None and prop.value:
                pid = int(prop.value[0])
                try:
                    import psutil

                    return psutil.Process(pid).name()
                except Exception:
                    return f"pid:{pid}"
        except Exception:
            pass
        try:
            prop = w.get_full_property(self._atom_wm_class, X.AnyPropertyType)
            if prop is not None and prop.value:
                raw = prop.value
                if isinstance(raw, bytes):
                    parts = raw.split(b"\x00")
                    if len(parts) >= 2:
                        return parts[1].decode("utf-8", "replace")
                    return raw.decode("utf-8", "replace")
        except Exception:
            pass
        return ""

    # ---- 枚举 ----

    def list_windows(self) -> list[WindowInfo]:
        items: list[WindowInfo] = []
        with self._lock:
            for w in self._client_windows():
                try:
                    attrs = w.get_attributes()
                    if attrs.map_state != X.IsViewable:
                        continue
                    if getattr(attrs, "override_redirect", 0):
                        continue
                    geom = w.get_geometry()
                    if geom.width <= 0 or geom.height <= 0:
                        continue
                    title = self._title(w)
                    if not title.strip():
                        continue
                    pos = self._root.translate_coords(w, 0, 0)
                    items.append(
                        WindowInfo(
                            handle=str(w.id),
                            title=title,
                            app=self._app(w),
                            rect=Rect(int(pos.x), int(pos.y), int(geom.width), int(geom.height)),
                        )
                    )
                except Exception:
                    continue
        items.sort(key=lambda i: i.title.lower())
        return items

    # ---- 抓取 ----

    def window_rect(self, handle: str) -> Rect:
        with self._lock:
            w = self._dpy.create_resource_object("window", int(handle))
            geom = w.get_geometry()
            pos = self._root.translate_coords(w, 0, 0)
            return Rect(int(pos.x), int(pos.y), int(geom.width), int(geom.height))

    def grab_window(self, handle: str, mode: str = "window") -> Image.Image:
        if mode == "screen":
            r = self.window_rect(handle)
            return self._grab_screen_rect(r)

        with self._lock:
            w = self._dpy.create_resource_object("window", int(handle))
            geom = w.get_geometry()
            width, height = int(geom.width), int(geom.height)
            if width <= 0 or height <= 0:
                raise RuntimeError(f"window has no area: {width}x{height}")
            raw = w.get_image(0, 0, width, height, X.ZPixmap, 0xFFFFFFFF)
            data = raw.data
            if isinstance(data, str):  # 老版本 python-xlib 返回 str
                data = data.encode("latin-1")

        bpl = len(data) // height if height else 0
        bpp = bpl // width if width else 0
        fmt = {4: "BGRX", 3: "BGR", 2: "BGRX"}.get(bpp)
        if fmt is None:
            raise RuntimeError(f"unsupported pixel layout: {bpp} bytes/px")
        return Image.frombuffer("RGB", (width, height), data, "raw", fmt, 0, 1).copy()

    def _grab_screen_rect(self, r: Rect) -> Image.Image:
        sct = self._mss()
        shot = sct.grab({"left": r.x, "top": r.y, "width": max(1, r.w), "height": max(1, r.h)})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    # ---- 输入 ----

    def _to_screen(self, handle: str, px: int, py: int) -> tuple[int, int]:
        with self._lock:
            w = self._dpy.create_resource_object("window", int(handle))
            pos = self._root.translate_coords(w, int(px), int(py))
            return int(pos.x), int(pos.y)

    def _toplevel(self, win: Any) -> Any:
        """找到窗口的顶层 frame（GNOME 等 reparenting WM 下客户区外面还套了一层）。"""
        cur = win
        for _ in range(16):
            try:
                parent = cur.query_tree().parent
            except Exception:
                return cur
            if parent is None or parent.id in (0, self._root.id):
                return cur
            cur = parent
        return cur

    def _active_window(self) -> int | None:
        try:
            prop = self._root.get_full_property(self._atom_active, X.AnyPropertyType)
            if prop is not None and prop.value:
                wid = int(prop.value[0])
                return wid or None
        except Exception:
            pass
        return None

    def _activate(self, win: Any) -> None:
        top = self._toplevel(win)
        # EWMH: _NET_ACTIVE_WINDOW 要发给 root，且 window 字段必须是「客户端窗口」
        try:
            ev = event.ClientMessage(
                window=win,
                client_type=self._atom_active,
                data=(32, [2, X.CurrentTime, 0, 0, 0]),  # 2 = 普通应用请求激活
            )
            self._root.send_event(
                ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask
            )
            self._dpy.sync()
        except Exception:
            pass
        # 兜底：raise 必须作用在顶层 frame 上，raise 客户区不会改变堆叠
        try:
            top.raise_window()
            self._dpy.sync()
        except Exception:
            pass

    def _pointer_window(self) -> Any:
        try:
            return self._root.query_pointer().child
        except Exception:
            return None

    def _is_on_top(self, win: Any) -> bool:
        """目标窗口的顶层 frame 是否就在指针下面（即点击能落到它身上）。"""
        child = self._pointer_window()
        if child is None:
            return False
        try:
            return child.id == self._toplevel(win).id
        except Exception:
            return False

    def _is_focused(self, win: Any) -> bool:
        """目标窗口是否已经有输入焦点。

        仅「在最上层」还不够：窗口没有焦点时，第一次点击会被 WM 吃掉用来聚焦，
        按钮本身不会响应。必须区分这两种情况。
        """
        active = self._active_window()
        if active is None:
            return False
        try:
            return active in (int(win.id), int(self._toplevel(win).id))
        except Exception:
            return False

    def _prepare_click(self, win: Any, sx: int, sy: int) -> int | None:
        """把指针移到目标处；必要时激活窗口。返回需要事后还原的窗口 id。"""
        self._root.warp_pointer(sx, sy)
        self._dpy.sync()
        time.sleep(0.03)

        if self._is_on_top(win) and self._is_focused(win):
            return None  # 已经既在最上层又有焦点，直接点，不闪烁

        prev = self._active_window() if _RESTORE_FOCUS_AFTER_CLICK else None
        self._activate(win)
        # WM 激活窗口时会短暂 server-grab，此时注入的 XTest 事件会被 WM 吞掉。
        # 必须等它完成（含动画）再重新对齐指针。
        time.sleep(0.35)
        self._root.warp_pointer(sx, sy)
        self._dpy.sync()
        time.sleep(0.08)
        return prev

    def _raw_click(self, sx: int, sy: int, xbtn: int, double: bool) -> None:
        self._root.warp_pointer(sx, sy)
        self._dpy.sync()
        time.sleep(0.03)
        for _ in range(2 if double else 1):
            xtest.fake_input(self._dpy, X.ButtonPress, xbtn)
            self._dpy.sync()
            time.sleep(0.03)
            xtest.fake_input(self._dpy, X.ButtonRelease, xbtn)
            self._dpy.sync()
            time.sleep(0.05)

    def click(self, handle: str, px: int, py: int, button: int = 1, double: bool = False) -> None:
        sx, sy = self._to_screen(handle, px, py)
        xbtn = _BUTTON_MAP.get(button, 1)
        with self._lock:
            win = self._dpy.create_resource_object("window", int(handle))
            prev = self._prepare_click(win, sx, sy) if _ACTIVATE_BEFORE_CLICK else None
            self._raw_click(sx, sy, xbtn, double)
            if prev and prev != int(handle):
                try:
                    self._activate(self._dpy.create_resource_object("window", prev))
                except Exception:
                    pass

    def scroll(self, handle: str, px: int, py: int, delta: int) -> None:
        sx, sy = self._to_screen(handle, px, py)
        xbtn = 4 if delta > 0 else 5
        with self._lock:
            win = self._dpy.create_resource_object("window", int(handle))
            prev = self._prepare_click(win, sx, sy) if _ACTIVATE_BEFORE_CLICK else None
            self._root.warp_pointer(sx, sy)
            self._dpy.sync()
            for _ in range(min(20, max(1, abs(int(delta))))):
                xtest.fake_input(self._dpy, X.ButtonPress, xbtn)
                xtest.fake_input(self._dpy, X.ButtonRelease, xbtn)
                self._dpy.sync()
                time.sleep(0.01)
            if prev and prev != int(handle):
                try:
                    self._activate(self._dpy.create_resource_object("window", prev))
                except Exception:
                    pass
