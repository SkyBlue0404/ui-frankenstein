#!/usr/bin/env python3
"""一键打包：清理上一轮的中间物 + 生成物，然后跑 PyInstaller。

    python scripts/build.py

产物在 dist/ 下（Linux: frankenstein，Windows: frankenstein.exe）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "frankenstein.spec"


def _rm(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
    print(f"  清理 {path.relative_to(ROOT)}")


def main() -> int:
    if not SPEC.exists():
        print(f"[!] 找不到打包配置: {SPEC}", file=sys.stderr)
        return 1

    print("== 清理构建中间物 ==")
    _rm(ROOT / "build")
    _rm(ROOT / "dist")

    print("== 清理 __pycache__ ==")
    for cache in (ROOT / "src").rglob("__pycache__"):
        _rm(cache)

    print("== 开始打包 ==")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)]
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print(f"[!] 打包失败（exit {rc}）", file=sys.stderr)
        return rc

    out = sorted((ROOT / "dist").glob("frankenstein*"))
    print("== 完成 ==")
    for p in out:
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size / 1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
