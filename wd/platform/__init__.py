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


__all__ = ["PlatformBackend", "get_backend"]
