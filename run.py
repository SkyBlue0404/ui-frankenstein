#!/usr/bin/env python3
"""Window Dashboard 启动入口。

    python run.py                      # 用同目录的 config.json
    python run.py -c my.json -p 9000   # 指定配置和端口
    python run.py --list-windows       # 只列出当前窗口，方便写配置
"""

from __future__ import annotations

import argparse
import json
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

sys.path.insert(0, str(ROOT))

# Windows 控制台默认是 cp1252 / cp936，打印中文标题（--list-windows、--help）
# 会直接抛 UnicodeEncodeError。冻结成 exe 后没有 PYTHONIOENCODING 兜底，
# 必须自己把标准输出切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from wd.config import Config  # noqa: E402
from wd.platform import get_backend  # noqa: E402


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把多个工具窗口的指定区域拼成一个自适应操作面板")
    parser.add_argument("-c", "--config", default=str(ROOT / "config.json"), help="配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址（默认取配置里的 server.host）")
    parser.add_argument("-p", "--port", type=int, default=None, help="监听端口（默认取配置里的 server.port）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
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
    args = parser.parse_args(argv)

    if args.list_windows:
        return cmd_list_windows()

    if args.pin:
        from wd.pin import run_pin

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
                from wd.config import DEFAULT_CONFIG

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

    from wd.server import create_app

    app = create_app(config_path)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"

    print(f"Window Dashboard  →  {url}")
    print(f"  编辑布局: {url}setup")
    print(f"  配置文件: {config_path.resolve()}")
    print("  Ctrl-C 退出")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
