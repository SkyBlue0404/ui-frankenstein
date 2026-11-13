#!/usr/bin/env python3
"""Window Dashboard 启动入口。

    python run.py                      # 用同目录的 config.json
    python run.py -c my.json -p 9000   # 指定配置和端口
    python run.py --list-windows       # 只列出当前窗口，方便写配置
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

# 冻结成单文件 exe 后，__file__ 指向的是临时解压目录；
# 用户配置必须落在 exe 旁边，否则每次运行都会「丢配置」。
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    _BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent
    _BUNDLE = ROOT
    # 源码布局：<root>/src/frankenstein/...
    sys.path.insert(0, str(ROOT / "src"))

# Windows 控制台默认是 cp1252 / cp936，打印中文标题（--list-windows、--help）
# 会直接抛 UnicodeEncodeError。冻结成 exe 后没有 PYTHONIOENCODING 兜底，
# 必须自己把标准输出切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from frankenstein.config import Config  # noqa: E402
from frankenstein.platform import get_backend  # noqa: E402


def cmd_list_windows() -> int:
    backend = get_backend()
    try:
        windows = backend.list_windows()
    finally:
        backend.close()
    if not windows:
        print("没有找到可见窗口。", file=sys.stderr)
        return 1
    width = max(len(w.title) for w in windows)
    width = min(width, 70)
    print(f"{'HANDLE':>12}  {'SIZE':>11}  TITLE")
    print("-" * (12 + 2 + 11 + 2 + width))
    for w in windows:
        size = f"{w.rect.w}x{w.rect.h}"
        print(f"{w.handle:>12}  {size:>11}  {w.title[:70]}")
        if w.app:
            print(f"{'':>12}  {'':>11}  ↳ {w.app}")
    return 0


# ---------------------------------------------------------------------------
# 打开界面
# ---------------------------------------------------------------------------

# Chromium 系浏览器：它们的 --app 模式能开出**没有地址栏和标签页**的窗口，
# 看起来就是个普通桌面程序，不需要用户自己找浏览器。
_CHROMIUM_NAMES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "brave-browser", "microsoft-edge", "microsoft-edge-stable", "vivaldi",
]
_CHROMIUM_WIN_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _find_chromium() -> str | None:
    for name in _CHROMIUM_NAMES:
        exe = shutil.which(name)
        if exe:
            return exe
    if sys.platform.startswith("win"):
        for path in _CHROMIUM_WIN_PATHS:
            if Path(path).exists():
                return path
    return None


def _spawn(cmd: list[str]) -> bool:
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform.startswith("win"):
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：关掉控制台也不带走窗口
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)
        return True
    except Exception:  # noqa: BLE001
        return False


def open_ui(url: str, mode: str = "app") -> bool:
    """打开操作界面。

    mode="app"     优先用 Chromium 的 --app 开无地址栏窗口（像桌面程序）
    mode="browser" 直接用系统默认浏览器

    返回是否成功开出了 app 窗口。
    """
    if mode == "app":
        exe = _find_chromium()
        if exe:
            if _spawn([
                exe,
                f"--app={url}",
                "--window-size=1320,900",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate",
            ]):
                return True
        print("[i] 没找到 Chrome / Edge / Chromium，改用系统默认浏览器打开")
    webbrowser.open(url)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把多个工具窗口的指定区域拼成一个自适应操作面板")
    parser.add_argument("-c", "--config", default=str(ROOT / "config.json"), help="配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址（默认取配置里的 server.host）")
    parser.add_argument("-p", "--port", type=int, default=None, help="监听端口（默认取配置里的 server.port）")
    parser.add_argument("--no-window", "--no-browser", dest="no_window", action="store_true",
                        help="启动后不打开界面窗口（纯服务模式）")
    parser.add_argument("--browser", action="store_true",
                        help="用系统默认浏览器打开，而不是无地址栏的 app 窗口")
    parser.add_argument("--list-windows", action="store_true", help="列出当前窗口后退出")
    parser.add_argument("--pin", metavar="TILE_ID", default=None,
                        help="以贴图模式启动：把这个 tile 钉在桌面最上层（Snipaste 风格）")
    parser.add_argument("--server", default="http://127.0.0.1:8777",
                        help="贴图模式连接的面板地址（默认 http://127.0.0.1:8777）")
    parser.add_argument("--scale", type=float, default=1.0, help="贴图缩放倍数（默认 1.0）")
    parser.add_argument("--pin-x", type=int, default=None, help="贴图窗口初始 X 坐标")
    parser.add_argument("--pin-y", type=int, default=None, help="贴图窗口初始 Y 坐标")
    parser.add_argument("--own-tile", action="store_true",
                        help="（内部用）关窗时回收服务端的瞬时 tile")
    parser.add_argument("--select", action="store_true",
                        help="（内部用）全屏框选一块区域后贴到桌面")
    parser.add_argument("--handle", default="", help="（内部用）框选要绑定到的窗口句柄")
    args = parser.parse_args(argv)

    if args.list_windows:
        return cmd_list_windows()

    if args.select:
        from frankenstein.overlay import run_selector

        return run_selector(args.server, args.handle)

    if args.pin:
        from frankenstein.pin import run_pin

        return run_pin(
            args.server, args.pin, scale=args.scale,
            x=args.pin_x, y=args.pin_y, own_tile=args.own_tile,
        )

    config_path = Path(args.config)
    if not config_path.exists():
        # 首次运行自动生成一份，exe 双击即用
        try:
            example = _BUNDLE / "config.example.json"
            if example.exists():
                config_path.write_text(example.read_text("utf-8"), "utf-8")
            else:
                from frankenstein.config import DEFAULT_CONFIG

                config_path.write_text(
                    json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), "utf-8"
                )
            print(f"[i] 已生成默认配置: {config_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[!] 无法生成配置 {config_path}: {exc}", file=sys.stderr)

    cfg = Config(config_path)
    if not cfg.tiles:
        print(f"[!] 配置里还没有 tile，先打开 http://{{host}}:{{port}}/setup 添加。", file=sys.stderr)

    host = args.host or cfg.server.get("host", "127.0.0.1")
    port = int(args.port or cfg.server.get("port", 8777))

    import uvicorn  # 延迟导入，--list-windows 时不需要

    from frankenstein.server import create_app

    app = create_app(config_path)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"

    print(f"Frankenstein  →  {url}")
    print(f"  编辑布局: {url}setup")
    print(f"  配置文件: {config_path.resolve()}")
    print("  Ctrl-C 退出")

    if not args.no_window:
        mode = "browser" if args.browser else "app"
        threading.Timer(0.8, lambda: open_ui(url, mode)).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
