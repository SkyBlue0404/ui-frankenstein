from __future__ import annotations

import sys

from .base import PlatformBackend


def get_backend() -> PlatformBackend:
    """按当前平台返回窗口后端。"""
    if sys.platform.startswith("win"):
        from .win32 import Win32Backend

        return Win32Backend()
    if sys.platform.startswith("linux"):
        from .x11 import X11Backend

        return X11Backend()
    raise RuntimeError(f"unsupported platform: {sys.platform}")


def get_hotkey_listener(callback, hotkey: str):
    """按当前平台返回全局快捷键监听器；不支持的平台返回 None。"""
    if sys.platform.startswith("win"):
        from .win32 import HotkeyListener as _HK
    elif sys.platform.startswith("linux"):
        from .x11 import HotkeyListener as _HK
    else:
        return None
    return _HK(callback, hotkey)


__all__ = ["PlatformBackend", "get_backend", "get_hotkey_listener"]
