"""系统托盘图标。

后台常驻时显示在顶部状态栏（GNOME / Windows 托盘区），右键菜单可以：
    - 打开面板（切到前台）
    - 截取区域（等同于按快捷键）
    - 设置快捷键（打开 /pins 页录制）
    - 退出后台

单独进程运行，通过 HTTP 和后台服务通信，互不影响。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

# 冻结版故意没把 gi / GTK 打进包里（会从 32MB 膨胀到 138MB）。
# 运行时从系统的 dist-packages 加载；没有就退回「不显示托盘」。
if getattr(sys, "frozen", False):
    for _cand in (
        "/usr/lib/python3/dist-packages",
        "/usr/lib/python3.12/dist-packages",
        "/usr/lib/python3.11/dist-packages",
    ):
        if os.path.isdir(_cand) and _cand not in sys.path:
            sys.path.append(_cand)

try:  # 没有 pystray / gi 就不显示托盘，功能其余部分照常
    import pystray
except Exception:  # noqa: BLE001
    pystray = None

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 图标
# ---------------------------------------------------------------------------


def _icon_image(size: int = 64) -> Image.Image:
    """和 assets/icon.svg 同款：蓝色圆角底 + 四块大小不一的面板 + 缝合线。"""
    s = size / 128.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(28 * s), fill=(76, 141, 255, 255))
    for x0, y0, x1, y1, a in (
        (20, 20, 66, 52, 255),
        (74, 20, 108, 52, 184),
        (20, 62, 52, 108, 184),
        (60, 62, 108, 108, 255),
    ):
        d.rounded_rectangle(
            [int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)],
            radius=int(7 * s),
            fill=(255, 255, 255, a),
        )
    # 缝合虚线
    y = int(57 * s)
    x = int(16 * s)
    while x < int(112 * s):
        d.rectangle([x, y, min(x + int(7 * s), size - 1), y + max(1, int(3 * s))],
                    fill=(11, 12, 15, 140))
        x += int(13 * s)
    return img


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _get_json(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _post(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _spawn_ui(page: str = "") -> None:
    """开一个面板窗口（复用后台实例）。"""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--open-only"]
    else:
        cmd = [sys.executable, str(ROOT / "run.py"), "--open-only"]
    if page:
        cmd += ["--page", page]
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 打开面板失败: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 托盘
# ---------------------------------------------------------------------------


class TrayApp:
    def __init__(self, server: str) -> None:
        self.server = server.rstrip("/")
        self.hotkey = "ctrl+alt+p"
        self._stop = threading.Event()
        self.icon = None

    # ---- 状态 ----

    def _refresh_hotkey(self) -> None:
        try:
            self.hotkey = (_get_json(f"{self.server}/api/state").get("hotkey") or self.hotkey)
        except Exception:  # noqa: BLE001
            pass

    def _pretty(self) -> str:
        names = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt",
                 "shift": "Shift", "super": "Super", "win": "Win"}
        return " + ".join(names.get(p.strip().lower(), p.strip().upper())
                          for p in self.hotkey.split("+") if p.strip())

    # ---- 菜单动作 ----

    def _open(self, *_args) -> None:
        _spawn_ui()

    def _settings(self, *_args) -> None:
        _spawn_ui("pins")

    def _capture(self, *_args) -> None:
        try:
            _post(f"{self.server}/api/capture-focus")
        except Exception as exc:  # noqa: BLE001
            print(f"[!] 截取失败: {exc}", file=sys.stderr)

    def _quit(self, *_args) -> None:
        try:
            _post(f"{self.server}/api/quit")
        except Exception:  # noqa: BLE001
            pass
        self._stop.set()
        if self.icon is not None:
            self.icon.stop()

    # ---- 生命周期 ----

    def _watchdog(self) -> None:
        """后台没了就自己退出，别留一个点不动的图标。"""
        misses = 0
        while not self._stop.is_set():
            time.sleep(3.0)
            try:
                _get_json(f"{self.server}/api/state", timeout=2.0)
                misses = 0
                self._refresh_hotkey()
            except Exception:  # noqa: BLE001
                misses += 1
                if misses >= 3:
                    self._stop.set()
                    if self.icon is not None:
                        self.icon.stop()
                    return

    def run(self) -> int:
        if pystray is None:
            print("[!] 没有 pystray，无法显示托盘（pip install pystray）", file=sys.stderr)
            return 1

        self._refresh_hotkey()
        menu = pystray.Menu(
            pystray.MenuItem("打开面板", self._open, default=True),
            pystray.MenuItem("截取区域", self._capture),
            pystray.MenuItem("设置快捷键", self._settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _item: f"快捷键：{self._pretty()}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 Frankenstein", self._quit),
        )
        self.icon = pystray.Icon("frankenstein", _icon_image(), "Frankenstein", menu)

        threading.Thread(target=self._watchdog, daemon=True, name="tray-watchdog").start()
        print("[i] 托盘已启动（右键图标可打开面板 / 截取 / 设置快捷键 / 退出）")
        self.icon.run()
        return 0


def run_tray(server: str) -> int:
    return TrayApp(server).run()
