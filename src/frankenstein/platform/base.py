from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image

from ..models import Rect, WindowInfo


class PlatformBackend(ABC):
    """跨平台窗口后端。

    约定：所有 `handle` 参数都是字符串形式的原生窗口 id；所有像素坐标
    都是相对于「整窗截图」左上角的坐标（不是 client 区，不是屏幕坐标），
    这样裁剪区域和点击回传可以共用同一套坐标。
    """

    name = "base"

    # 点击注入方式：
    #   "event"    —— 事件投递（X11: XSendEvent / Windows: PostMessage），
    #                 **不移动物理鼠标**，默认。
    #   "physical" —— 物理注入（X11: warp+XTest / Windows: SetCursorPos+mouse_event），
    #                 兼容性最好，但会把用户的鼠标拽到目标窗口上。
    click_mode = "event"

    # ---- 枚举 ----

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]:
        """列出当前所有可见的顶层窗口。"""

    def focused_window(self) -> WindowInfo | None:
        """当前拥有输入焦点的窗口（模式 A 的快捷键截取用）。"""
        return None

    # ---- 抓取 ----

    @abstractmethod
    def window_rect(self, handle: str) -> Rect:
        """窗口在屏幕上的绝对矩形。"""

    @abstractmethod
    def grab_window(
        self, handle: str, mode: str = "window", box: tuple[int, int, int, int] | None = None
    ) -> Image.Image:
        """抓取窗口画面。

        mode="window": 抓窗口自身内容，窗口被遮挡也能抓到（推荐）。
        mode="screen": 抓窗口所在的屏幕区域，永远正确但要求窗口可见。
        box=(x, y, w, h): 只抓窗口内的这一块（窗口坐标）。给了它就别抓整窗，
                          大窗口整窗抓取非常慢。
        """

    # ---- 输入 ----

    @abstractmethod
    def click(
        self,
        handle: str,
        px: int,
        py: int,
        button: int = 1,
        double: bool = False,
    ) -> None:
        """在整窗截图的 (px, py) 像素处点击。button: 1=左 2=中 3=右。"""

    @abstractmethod
    def scroll(self, handle: str, px: int, py: int, delta: int) -> None:
        """在 (px, py) 处滚轮。delta > 0 上滚，< 0 下滚。"""

    # ---- 工具 ----

    def close(self) -> None:  # pragma: no cover - 由子类按需覆盖
        pass
