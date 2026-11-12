from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from PIL import Image

from ..models import Rect, WindowInfo
from .base import PlatformBackend

# ---------------------------------------------------------------------------
# Win32 绑定（纯 ctypes，不依赖 pywin32）
# ---------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
DWMWA_CLOAKED = 14

WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
MK_LBUTTON, MK_RBUTTON, MK_MBUTTON = 0x0001, 0x0002, 0x0010

_DOWN = {1: WM_LBUTTONDOWN, 2: WM_MBUTTONDOWN, 3: WM_RBUTTONDOWN}
_UP = {1: WM_LBUTTONUP, 2: WM_MBUTTONUP, 3: WM_RBUTTONUP}
_MK = {1: MK_LBUTTON, 2: MK_MBUTTON, 3: MK_RBUTTON}
_MEF_DOWN = {1: 0x0002, 2: 0x0020, 3: 0x0008}
_MEF_UP = {1: 0x0004, 2: 0x0040, 3: 0x0010}


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ScreenToClient.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

# 64 位下必须声明 argtypes，否则 ctypes 会把 HWND 当 32 位 int 传，句柄被截断。
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.mouse_event.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
]
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

GA_ROOT = 2


def _enable_dpi_awareness() -> None:
    """必须在任何 GetWindowRect 之前调用，否则拿到的是被虚拟化的坐标。"""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
    except Exception:
        pass


class Win32Backend(PlatformBackend):
    name = "win32"

    def __init__(self) -> None:
        _enable_dpi_awareness()
        self._lock = threading.RLock()
        self._dwm = None
        try:
            self._dwm = ctypes.WinDLL("dwmapi")
            self._dwm.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ]
            self._dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        except Exception:
            self._dwm = None

    # ---- 内部 ----

    def _is_cloaked(self, hwnd: int) -> bool:
        if self._dwm is None:
            return False
        val = wintypes.DWORD(0)
        try:
            hr = self._dwm.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), wintypes.DWORD(DWMWA_CLOAKED),
                ctypes.byref(val), ctypes.sizeof(val),
            )
            return hr == 0 and val.value != 0
        except Exception:
            return False

    def _ex_style(self, hwnd: int) -> int:
        return user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)

    def _proc_name(self, hwnd: int) -> str:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        try:
            import psutil

            return psutil.Process(pid.value).name()
        except Exception:
            return f"pid:{pid.value}"

    def _dib_to_image(self, hdc: int, bmp: int, w: int, h: int) -> Image.Image:
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h  # 负数 = top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0  # BI_RGB

        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1)
        return img.convert("RGB")

    # ---- 枚举 ----

    def list_windows(self) -> list[WindowInfo]:
        items: list[WindowInfo] = []
        with self._lock:
            def cb(hwnd: int, _lparam: int) -> bool:
                try:
                    h = int(hwnd)
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    title_len = user32.GetWindowTextLengthW(hwnd)
                    if title_len <= 0:
                        return True
                    buf = ctypes.create_unicode_buffer(title_len + 1)
                    user32.GetWindowTextW(hwnd, buf, title_len + 1)
                    title = buf.value.strip()
                    if not title:
                        return True
                    ex = self._ex_style(h)
                    if ex & WS_EX_TOOLWINDOW and not (ex & WS_EX_APPWINDOW):
                        return True
                    if self._is_cloaked(h):
                        return True
                    r = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(r))
                    w, hgt = r.right - r.left, r.bottom - r.top
                    if w <= 0 or hgt <= 0:
                        return True
                    items.append(
                        WindowInfo(
                            handle=str(h),
                            title=title,
                            app=self._proc_name(h),
                            rect=Rect(r.left, r.top, w, hgt),
                        )
                    )
                except Exception:
                    pass
                return True

            user32.EnumWindows(WNDENUMPROC(cb), 0)

        items.sort(key=lambda i: i.title.lower())
        return items

    # ---- 抓取 ----

    def window_rect(self, handle: str) -> Rect:
        r = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(int(handle)), ctypes.byref(r))
        return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)

    def grab_window(self, handle: str, mode: str = "window") -> Image.Image:
        hwnd = int(handle)
        with self._lock:
            r = wintypes.RECT()
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
                raise RuntimeError(f"GetWindowRect failed for hwnd {handle}")
            w, h = r.right - r.left, r.bottom - r.top
            if w <= 0 or h <= 0:
                raise RuntimeError(f"window has no area: {w}x{h}")

            if mode == "screen":
                return self._grab_screen_rect(int(handle), Rect(r.left, r.top, w, h))

            hdc = user32.GetWindowDC(wintypes.HWND(hwnd))
            mem = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = gdi32.SelectObject(mem, bmp)
            try:
                ok = user32.PrintWindow(wintypes.HWND(hwnd), mem, PW_RENDERFULLCONTENT)
                if not ok:
                    # 部分老程序不支持 PW_RENDERFULLCONTENT，退回 0
                    user32.PrintWindow(wintypes.HWND(hwnd), mem, 0)
                return self._dib_to_image(mem, bmp, w, h)
            finally:
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(wintypes.HWND(hwnd), hdc)

    def _grab_screen_rect(self, hwnd: int, r: Rect) -> Image.Image:
        sdc = user32.GetDC(wintypes.HWND(0))
        mem = gdi32.CreateCompatibleDC(sdc)
        bmp = gdi32.CreateCompatibleBitmap(sdc, r.w, r.h)
        old = gdi32.SelectObject(mem, bmp)
        try:
            gdi32.BitBlt(mem, 0, 0, r.w, r.h, sdc, r.x, r.y, SRCCOPY | CAPTUREBLT)
            return self._dib_to_image(mem, bmp, r.w, r.h)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(wintypes.HWND(0), sdc)

    # ---- 输入 ----

    def _screen_point(self, handle: str, px: int, py: int) -> tuple[int, int]:
        r = self.window_rect(handle)
        return r.x + int(px), r.y + int(py)

    def _is_on_top(self, hwnd: int, sx: int, sy: int) -> bool:
        """屏幕点 (sx, sy) 上的窗口是不是目标窗口（或它的子窗口）。

        Windows 和 X11 一样：鼠标注入是屏幕坐标的，被遮挡的窗口收不到点击，
        必须先把它提到最前。
        """
        hit = user32.WindowFromPoint(wintypes.POINT(sx, sy))
        if not hit:
            return False
        target = wintypes.HWND(hwnd)
        if hit == target or user32.IsChild(target, hit):
            return True
        try:
            return user32.GetAncestor(hit, GA_ROOT) == user32.GetAncestor(target, GA_ROOT)
        except Exception:
            return False

    def _is_focused(self, hwnd: int) -> bool:
        """目标窗口是否已经是前台窗口。

        只判断 Z 序不够：窗口没有焦点时，第一次点击会被系统用来激活它。
        """
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        target = wintypes.HWND(hwnd)
        if fg == target or user32.IsChild(target, fg):
            return True
        try:
            return user32.GetAncestor(fg, GA_ROOT) == user32.GetAncestor(target, GA_ROOT)
        except Exception:
            return False

    def _raise(self, hwnd: int) -> None:
        h = wintypes.HWND(hwnd)
        try:
            user32.SetForegroundWindow(h)
        except Exception:
            pass
        try:
            user32.BringWindowToTop(h)
        except Exception:
            pass

    def _prepare_click(self, hwnd: int, sx: int, sy: int) -> None:
        user32.SetCursorPos(sx, sy)
        if self._is_on_top(hwnd, sx, sy) and self._is_focused(hwnd):
            time.sleep(0.02)
            return
        self._raise(hwnd)
        # 等 WM 完成激活（含 Z 序重排），否则注入的点击会被用来聚焦窗口
        time.sleep(0.25)
        user32.SetCursorPos(sx, sy)
        time.sleep(0.08)

    def click(self, handle: str, px: int, py: int, button: int = 1, double: bool = False) -> None:
        hwnd = int(handle)
        sx, sy = self._screen_point(handle, px, py)
        with self._lock:
            self._prepare_click(hwnd, sx, sy)
            for _ in range(2 if double else 1):
                user32.mouse_event(_MEF_DOWN.get(button, _MEF_DOWN[1]), 0, 0, 0, 0)
                time.sleep(0.02)
                user32.mouse_event(_MEF_UP.get(button, _MEF_UP[1]), 0, 0, 0, 0)
                time.sleep(0.05)

    def scroll(self, handle: str, px: int, py: int, delta: int) -> None:
        hwnd = int(handle)
        sx, sy = self._screen_point(handle, px, py)
        with self._lock:
            self._prepare_click(hwnd, sx, sy)
            steps = max(1, min(20, abs(int(delta))))
            for _ in range(steps):
                user32.mouse_event(0x0800, 0, 0, 120 if delta > 0 else -120, 0)  # MOUSEEVENTF_WHEEL
                time.sleep(0.01)

    # ---- 备用：PostMessage 模式（不移动真实鼠标，但兼容性较差）----

    def post_click(self, handle: str, px: int, py: int, button: int = 1) -> None:
        hwnd = int(handle)
        r = self.window_rect(handle)
        pt = wintypes.POINT(r.x + int(px), r.y + int(py))
        user32.ScreenToClient(wintypes.HWND(hwnd), ctypes.byref(pt))
        lparam = (pt.y << 16) | (pt.x & 0xFFFF)
        down, up, mk = _DOWN.get(button, WM_LBUTTONDOWN), _UP.get(button, WM_LBUTTONUP), _MK.get(button, MK_LBUTTON)
        user32.PostMessageW(wintypes.HWND(hwnd), down, mk, lparam)
        user32.PostMessageW(wintypes.HWND(hwnd), up, 0, lparam)
