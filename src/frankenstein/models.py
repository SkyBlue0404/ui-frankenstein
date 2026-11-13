from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Rect:
    """屏幕绝对坐标下的矩形。"""

    x: int
    y: int
    w: int
    h: int

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class WindowInfo:
    """一个可被纳入仪表盘的工具窗口。"""

    handle: str          # 平台原生句柄（Windows: HWND 十进制; X11: window id 十进制）
    title: str
    app: str             # 进程名 / WM_CLASS
    rect: Rect           # 窗口在屏幕上的绝对矩形

    def as_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "title": self.title,
            "app": self.app,
            "rect": self.rect.as_dict(),
        }
