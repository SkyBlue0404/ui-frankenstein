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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

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
    args = parser.parse_args(argv)

    if args.list_windows:
        return cmd_list_windows()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[!] 配置文件不存在: {config_path}", file=sys.stderr)
        print(f"    可以从 config.example.json 复制一份：cp config.example.json {config_path.name}", file=sys.stderr)

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
