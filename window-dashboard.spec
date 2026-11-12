# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Linux / Windows 通用）。

    pyinstaller window-dashboard.spec

产物是单文件可执行程序，放在 dist/ 下：
    Linux   → dist/window-dashboard
    Windows → dist/window-dashboard.exe
"""

from PyInstaller.utils.hooks import collect_submodules

# uvicorn 是延迟导入的（run.py 里 import uvicorn），静态分析跟不到，
# 必须显式收集它按字符串动态加载的 loop / protocol / lifespan 实现。
hiddenimports = collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # Pillow 的 Tk 集成靠一个动态导入的模块，静态分析发现不了，
    # 不显式加进来，贴图窗口在打包版里会报 No module named 'PIL._tkinter_finder'
    "PIL._tkinter_finder",
]

# 平台相关的可选依赖：Linux 用 Xlib，Windows 用 ctypes（内置）。
# 缺哪个都不该让打包失败，所以不写进 hiddenimports。
excludes = [
    "pytest",
    "_pytest",
    "pytest_asyncio",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "IPython",
]

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("wd/static", "wd/static"),
        ("config.example.json", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="window-dashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
