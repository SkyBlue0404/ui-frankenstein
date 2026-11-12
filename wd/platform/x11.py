from __future__ import annotations

import select
import sys
import threading
import time
from typing import Any

from PIL import Image

from ..models import Rect, WindowInfo
from .base import PlatformBackend

# 这些 import 只在 Linux 上有意义，放在模块级由 platform/__init__ 懒加载
from Xlib import X, XK, display  # type: ignore
from Xlib.ext import xtest  # type: ignore
from Xlib.protocol import event  # type: ignore

_BUTTON_MAP = {1: 1, 2: 2, 3: 3}  # our button -> X button

# XTest 注入的是「屏幕坐标」，点击永远落在最上层的窗口上。
# 所以目标窗口被遮挡时必须先把它抬到最前，点完再还原焦点，
# 否则点击会打到浏览器/编辑器上（而不是被拼接的工具窗口）。
_ACTIVATE_BEFORE_CLICK = True
_RESTORE_FOCUS_AFTER_CLICK = True
# 很多程序（GTK / Qt / Electron）收到 ButtonPress 后会主动把自己 raise 到最前
# 并抢焦点。用户要的是「后台操作」，所以点完必须把它塞回原来的堆叠位置。
_RESTORE_STACK_AFTER_CLICK = True


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
        # "event"    = XSendEvent 投递（不动鼠标，默认）
        # "physical" = warp + XTest 物理注入（兼容自绘 UI，但会拽动鼠标）
        self.click_mode = "event"
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

    def focused_window(self) -> WindowInfo | None:
        """当前聚焦的窗口（模式 A 快捷键截取用）。

        会被快捷键线程调用，而 Xlib 连接不是线程安全的，所以整段加锁。
        """
        try:
            with self._lock:
                active = self._active_window()
                if active is None:
                    return None
                wins = self.list_windows()
                for w in wins:
                    if int(w.handle) == active:
                        return w
                # _NET_ACTIVE_WINDOW 有时给的是 frame 而不是 client
                for w in wins:
                    try:
                        win = self._dpy.create_resource_object("window", int(w.handle))
                        if self._toplevel(win).id == active:
                            return w
                    except Exception:
                        continue
        except Exception:
            pass
        return None

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

    def _send_button(
        self, win: Any, press: bool, detail: int,
        cx: int, cy: int, sx: int, sy: int,
    ) -> None:
        """用 XSendEvent 把按钮事件直接投递给目标窗口。

        关键点：**完全不移动物理鼠标**。XTest 的物理注入必须先把指针移到目标
        位置，用户会看到鼠标被拽走；XSendEvent 走的是纯事件通道，光标原地不动。
        GTK / Qt / Electron 都会对事件自己做内部命中测试，所以发到顶层客户端
        窗口即可，不需要精确定位到子控件。
        """
        cls = event.ButtonPress if press else event.ButtonRelease
        ev = cls(
            time=X.CurrentTime,
            root=self._root,
            window=win,
            same_screen=1,
            child=X.NONE,
            root_x=sx,
            root_y=sy,
            event_x=cx,
            event_y=cy,
            state=0,
            detail=detail,
        )
        mask = X.ButtonPressMask if press else X.ButtonReleaseMask
        win.send_event(ev, event_mask=mask, propagate=False)
        self._dpy.sync()

    def _stack_above(self, frame: Any) -> tuple[int | None, bool]:
        """记录 frame 在堆叠顺序里「上面的那个兄弟」。

        还原时把 frame 插回这个兄弟的下面，就回到原来的层号。
        如果 frame 已经是最上层，就没有「被抬起来」这回事，不用还原。
        """
        try:
            kids = self._root.query_tree().children
            ids = [c.id for c in kids]
            if frame.id not in ids:
                return None, False
            i = ids.index(frame.id)
            if i >= len(kids) - 1:
                return None, False
            return kids[i + 1].id, True
        except Exception:
            return None, False

    def _restore_stack(self, frame: Any, above_id: int | None, ok: bool) -> None:
        """把 window 插回到 above_id 的下面。"""
        if not ok:
            return
        try:
            sibling = self._dpy.create_resource_object("window", int(above_id))
            frame.configure(stack_mode=X.Below, sibling=sibling)
            self._dpy.sync()
        except Exception:
            pass

    def click(self, handle: str, px: int, py: int, button: int = 1, double: bool = False) -> None:
        xbtn = _BUTTON_MAP.get(button, 1)
        with self._lock:
            win = self._dpy.create_resource_object("window", int(handle))
            sx, sy = self._to_screen(handle, px, py)

            if self.click_mode == "event":
                frame = self._toplevel(win)
                above, stack_ok = self._stack_above(frame)
                prev = self._active_window() if _RESTORE_FOCUS_AFTER_CLICK else None

                self._ensure_active(win, frame, above, stack_ok)

                for _ in range(2 if double else 1):
                    self._send_button(win, True, xbtn, px, py, sx, sy)
                    time.sleep(0.02)
                    self._send_button(win, False, xbtn, px, py, sx, sy)
                    time.sleep(0.03)
                self._restore_after_click(frame, above, stack_ok, prev, int(handle))
                return

            # 物理注入兜底：某些自绘 UI 会忽略合成事件
            prev = self._prepare_click(win, sx, sy) if _ACTIVATE_BEFORE_CLICK else None
            self._raw_click(sx, sy, xbtn, double)
            if prev and prev != int(handle):
                try:
                    self._activate(self._dpy.create_resource_object("window", prev))
                except Exception:
                    pass

    def _ensure_active(
        self, win: Any, frame: Any, above_id: int | None, stack_ok: bool
    ) -> None:
        """保证目标窗口处于激活状态，然后立刻把它塞回原堆叠位置。

        Chromium / Electron（VS Code、Chrome 等）**只处理「自己处于激活状态」
        时的合成事件**，不激活就完全忽略。而激活必然伴随一次 raise，所以这里
        「激活 → 马上塞回去」，把窗口闪到前台的时间压到最短（约 0.25s），
        既让点击生效，又不会让它停在最前面。
        """
        if self._is_focused(win):
            return  # 本来就激活，不闪
        self._activate(win)
        time.sleep(0.18)  # 等 WM 把激活请求处理完
        self._restore_stack(frame, above_id, stack_ok)
        time.sleep(0.06)

    def _restore_after_click(
        self, frame: Any, above_id: int | None, stack_ok: bool, prev: int | None, handle: int
    ) -> None:
        """点击后把被程序自己抬起来的窗口塞回去，并还原输入焦点。

        程序处理点击是**异步**的：它可能在 ButtonPress 之后几十毫秒才去
        `_NET_ACTIVE_WINDOW` / raise，只还原一次会被它盖掉。所以这里做
        「还原 → 校验 → 不稳就再来一轮」的收敛循环。
        """
        if not (_RESTORE_STACK_AFTER_CLICK or _RESTORE_FOCUS_AFTER_CLICK):
            return

        want_focus = _RESTORE_FOCUS_AFTER_CLICK and prev is not None and prev != handle
        for _ in range(4):
            time.sleep(0.07)
            if _RESTORE_STACK_AFTER_CLICK:
                self._restore_stack(frame, above_id, stack_ok)
            if want_focus and self._active_window() != prev:
                try:
                    self._activate(self._dpy.create_resource_object("window", int(prev)))
                except Exception:
                    pass
            if not want_focus or self._active_window() == prev:
                break

    def scroll(self, handle: str, px: int, py: int, delta: int) -> None:
        xbtn = 4 if delta > 0 else 5
        steps = min(20, max(1, abs(int(delta))))
        with self._lock:
            win = self._dpy.create_resource_object("window", int(handle))
            sx, sy = self._to_screen(handle, px, py)

            if self.click_mode == "event":
                frame = self._toplevel(win)
                above, stack_ok = self._stack_above(frame)
                prev = self._active_window() if _RESTORE_FOCUS_AFTER_CLICK else None

                self._ensure_active(win, frame, above, stack_ok)

                for _ in range(steps):
                    self._send_button(win, True, xbtn, px, py, sx, sy)
                    self._send_button(win, False, xbtn, px, py, sx, sy)
                    time.sleep(0.01)
                self._restore_after_click(frame, above, stack_ok, prev, int(handle))
                return

            sx, sy = self._to_screen(handle, px, py)
            prev = self._prepare_click(win, sx, sy) if _ACTIVATE_BEFORE_CLICK else None
            self._root.warp_pointer(sx, sy)
            self._dpy.sync()
            for _ in range(steps):
                xtest.fake_input(self._dpy, X.ButtonPress, xbtn)
                xtest.fake_input(self._dpy, X.ButtonRelease, xbtn)
                self._dpy.sync()
                time.sleep(0.01)
            if prev and prev != int(handle):
                try:
                    self._activate(self._dpy.create_resource_object("window", prev))
                except Exception:
                    pass

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


# ---------------------------------------------------------------------------
# 全局快捷键
# ---------------------------------------------------------------------------

# 默认 Ctrl+Alt+P。Mod1Mask 在 X11 上就是 Alt。
DEFAULT_HOTKEY = "ctrl+alt+p"
_MOD_ALIASES = {
    "ctrl": X.ControlMask,
    "control": X.ControlMask,
    "alt": X.Mod1Mask,
    "shift": X.ShiftMask,
    "super": X.Mod4Mask,
    "win": X.Mod4Mask,
}


def parse_hotkey(spec: str) -> tuple[str, int] | None:
    """"ctrl+alt+p" -> ("p", ControlMask|Mod1Mask)。解析失败返回 None。"""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    key = None
    for part in parts:
        if part in _MOD_ALIASES:
            mods |= _MOD_ALIASES[part]
        else:
            key = part
    if not key:
        return None
    return key, mods


class HotkeyListener(threading.Thread):
    """全局快捷键监听。

    用**独立的 X 连接**，避免和抓帧线程抢同一个 Display（Xlib 不是线程安全的）。
    快捷键按下后回调在监听线程里执行，回调内部要自己保证线程安全。
    """

    def __init__(self, callback: Any, hotkey: str = DEFAULT_HOTKEY) -> None:
        super().__init__(daemon=True, name="hotkey")
        self._cb = callback
        self._hotkey = hotkey
        self._stop = threading.Event()
        self._dpy = display.Display()
        self._root = self._dpy.screen().root
        self.ok = False

    def run(self) -> None:
        parsed = parse_hotkey(self._hotkey)
        if not parsed:
            print(f"[!] 无法解析快捷键: {self._hotkey}", file=sys.stderr)
            return
        key_name, mods = parsed
        keycode = self._dpy.keysym_to_keycode(XK.string_to_keysym(key_name))
        if not keycode:
            print(f"[!] 找不到键位: {key_name}", file=sys.stderr)
            return

        # CapsLock / NumLock 会改动 modifier 状态位，这几种组合都要 grab
        grabbed = 0
        for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            try:
                self._root.grab_key(
                    keycode, mods | extra, True, X.GrabModeAsync, X.GrabModeAsync
                )
                grabbed += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[!] grab_key 失败: {exc}", file=sys.stderr)
        self._dpy.sync()
        self.ok = grabbed > 0
        if not self.ok:
            return
        print(f"[i] 全局快捷键已注册: {self._hotkey.upper()}")

        try:
            fd = self._dpy.fileno()
        except Exception:  # noqa: BLE001
            return

        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                while self._dpy.pending_events():
                    evt = self._dpy.next_event()
                    if evt.type == X.KeyPress:
                        try:
                            self._cb()
                        except Exception as exc:  # noqa: BLE001
                            print(f"[!] 快捷键回调失败: {exc}", file=sys.stderr)
            except Exception:  # noqa: BLE001
                break

        try:
            self._dpy.close()
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self._stop.set()
