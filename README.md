# Frankenstein

> 缝合怪 —— 把好几个进程的界面缝到一起。

把多个正在运行的工具窗口里**你关心的那一小块**（比如操作按钮栏、状态灯、日志尾部）抠出来，
拼成一个**自适应**的网页面板。页面上点一下，事件会**回传到原窗口**——
相当于把一堆分散的进程控制台，集成成一个大按钮面板。

支持 **Linux (X11)** 和 **Windows**。

```
┌──────────────────────────────────────────────────────────┐
│ 工具A 窗口                          ┌─────────────────┐ │
│  ┌───────────────────────────────┐  │  ┌───────────┐  │ │
│  │ [开始] [停止] [重置]   ● 运行中│──┼─▶│ [开始][停止]│  │ │
│  └───────────────────────────────┘  │  └───────────┘  │ │
│                                     │  ┌───────────┐  │ │
│ 工具B 窗口                          │  │ ● 运行中   │  │ │
│  ┌───────────────────────────────┐  │  └───────────┘  │ │
│  │ ● 就绪   [连接] [断开]         │──┼─▶  ……          │ │
│  └───────────────────────────────┘  │                 │ │
└──────────────────────────────────────────────────────┘ │
       原窗口（可以最小化/藏起来）      自适应拼接面板     │
```

## 原理

| 层 | 做的事 |
|---|---|
| 抓帧线程 | 每个 tile 一个后台线程，按帧率把「窗口 → 裁剪区域 → JPEG」推到内存槽位 |
| HTTP 流 | `GET /stream/{id}` 以 MJPEG 推给浏览器 `<img>`，慢客户端只会丢帧，不会拖垮抓帧 |
| 点击回传 | 浏览器把点击坐标换算成裁剪图像素 → 后端换算成窗口绝对坐标 → 注入系统事件 |
| 自适应 | CSS Grid `auto-fill` + `object-fit: contain`，任意屏幕宽度自动换列，坐标按缩放比反算 |

## 安装 / 运行

**它是一个单文件程序，不需要你自己开浏览器。** 双击或命令行启动后会自动弹出一个
**没有地址栏和标签页的窗口**（用 Chrome / Edge 的 `--app` 模式开），看起来就是个普通桌面程序。

### Linux —— 安装到系统

```bash
python scripts/build.py     # 先打包
./scripts/install.sh        # 装到 ~/.local：可执行文件 + 应用菜单项 + 图标
```

装完在应用菜单里搜 **Frankenstein / 缝合怪** 就能启动。卸载：

```bash
./scripts/install.sh --uninstall
```

### 前台 / 后台两种形态

**后台模式**（登录自启）：顶部状态栏（Windows 是右下角托盘区）常驻一个
**Frankenstein 图标**。不占屏幕、不弹窗口。右键图标：

- **打开面板** —— 切到前台
- **截取区域** —— 等同按快捷键
- **设置快捷键** —— 打开 `/pins` 录制
- **退出 Frankenstein**

**前台模式**：打开的面板是独立窗口（无地址栏、无标签页），
并且在左侧 **dock 里有自己的 Frankenstein 图标** —— 因为窗口用独立浏览器 profile
启动了 `--class=frankenstein`，配合 `.desktop` 的 `StartupWMClass` 被识别成独立应用。

```bash
frankenstein              # 启动并打开面板（后台已在跑就只弹面板）
frankenstein --tray       # 后台常驻 + 托盘图标（自启用的就是这条）
frankenstein --no-window  # 纯后台，连托盘都不要
frankenstein --quit       # 关掉后台实例
frankenstein --open-only --page pins   # 只打开 /pins 页
```

不想要自启：`./scripts/install.sh --no-autostart`。

> 托盘依赖 `pystray`，Linux 上还需要系统的 `gir1.2-appindicator3-0.1`
> 和 `python3-gi`（Ubuntu 通常自带）。打包版**故意没有**把 GTK 打进去
> （会让单文件从 37MB 膨胀到 138MB），运行时从系统加载；没有就静默不显示托盘，
> 其余功能照常。

### 快捷键可自定义

打开 **`/pins`** 页，点一下顶部的快捷键按钮，直接按你想用的组合 —— 立即生效并写进
`config.json`。也可以直接改配置里的 `defaults.hotkey`。

最少要有一个修饰键（Ctrl / Alt / Shift / Super），支持 `f1`~`f24`、方向键、
`space`、`tab`、`enter` 等。

### 任意平台 —— 直接跑单文件

| 平台 | 产物 | 用法 |
|---|---|---|
| Linux | `dist/frankenstein` | `./dist/frankenstein` |
| Windows | `dist/frankenstein.exe` | 双击 |

首次运行会在可执行文件旁边自动生成 `config.json`。

### 从源码运行

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

### 启动参数

| 参数 | 说明 |
|---|---|
| （默认） | 开一个无地址栏的 app 窗口 |
| `--browser` | 用系统默认浏览器打开（带地址栏） |
| `--no-window` | 不打开任何窗口，纯后台守护（配合自启） |
| `--open-only` | 只打开面板，不启动服务（要求后台已在跑） |
| `--quit` | 退出后台实例 |
| `--list-windows` | 列出当前窗口后退出，方便写匹配正则 |
| `-p 9000` | 换端口 |

端口上已经有实例在跑时，再启动会**直接复用**，只把面板弹出来，不会报端口占用。

## 使用流程

1. 打开 **`/setup`**。
2. 左侧点选一个工具窗口 → 右侧显示它的整窗截图。
3. 在截图上**拖拽**框出要显示的区域（比如按钮栏那一行）。
4. 填标题、帧率 → **保存 tile**。
5. 回到 **`/`**，就是一个自适应面板了。点按钮 = 点原窗口。

## 贴图（Snipaste 风格）

除了网页面板，还可以把任意 tile **钉在桌面最上层**，当作一个独立的小窗口用：

- 面板上每个 tile 右上角有 📌 按钮，点一下就在本机弹出贴图窗口
- 或者命令行：`python run.py --pin <tile-id> --scale 1.5`
- 贴图显示的是**实时画面**（不是静态截图），原窗口里按钮状态变化会同步反映
- **左键单击** → 直接点原窗口对应位置；**拖拽** → 移动贴图；**滚轮** → 原窗口滚动
- **右键** → 菜单：放大 / 缩小 / 原始大小 / 调不透明度 / 关闭
- 无边框、置顶，会自动跟随原窗口尺寸变化

> 贴图窗口是原生 Tk 窗口，需要服务器那台机器有图形界面（X11 / Windows 桌面）。
> 如果只想要一个静态截图，`--pin` 之外的行为和 Snipaste 的「贴图」是一样的。

## 两种工作模式

顶栏可以在两种模式间切换，**两者都不会把原窗口唤到前台**。

### 模式 A：贴图（Snipaste 后台操作）

`/pins` —— 按 **`Ctrl + Alt + P`** 弹出全屏遮罩，框选一块区域再贴到桌面最上层。

交互和 Snipaste 一致：

1. **拖拽拉出第一版选框**（松手不会立刻截取）
2. 选框进入可编辑状态：
   - 拖框内任意位置 → **移动**
   - 拖 **8 个手柄**（四角 + 四边） → **缩放**
   - 拖框外空白 → 重新拉框
3. **双击框内** / 按 **Enter** / 点 **「✓ 完成」** → 提交
4. **Esc** / 点 **「✕ 取消」** → 放弃

遮罩显示按下快捷键那一刻的整屏画面，目标窗口保持高亮，其余压暗。
选区被限制在目标窗口内。
- 贴上去的是**实时画面**，原程序状态变化同步可见
- 直接点它就能操作，鼠标不动、原窗口不跳前台
- 页面上能看到当前所有贴图，可以单独关闭

### 模式 B：画布（上位机布局）

`/` —— 一块空白画布，随便把哪个进程的哪一块摆到哪个位置。

- **没有标题栏**，画面占满整个面板；工具条（📌 贴到桌面 / 缩放 / ✕ 移除）悬停时才浮出来
- 面板尺寸默认**跟随裁剪画面的实际像素**（超过 560px 宽等比缩下来），
  也可以在工具条里选 50% ~ 300% 缩放
- 面板**拖拽移动**、**8 个手柄缩放**，位置自动保存（拖动超过 4px 才算拖拽，
  否则按点击回传给原程序）
- **▦ 调整区域**：进入后拖四边四角改的是**源窗口的截取范围**（不是显示大小），
  面板按当前缩放跟着变大变小 —— 第一次框完不满意可以直接再调
- 「＋ 添加窗口」→ 选进程 → 截图上拖拽框选区域 → 创建面板
- 右上角「自动排列」一键网格化

> 两种模式的点击都是 `XSendEvent` / `PostMessage` 直接投递，物理鼠标不动。
> Chromium / Electron 程序因为只处理「激活状态」的合成事件，会有约 200ms 的
> 闪一下（详见下面的限制说明）。

## 布局编辑器

`/setup` 用来精细调整单个 tile：左边选窗口，右边拖拽框出区域。
**选框支持拖动位置和 8 向缩放**，右下角面板里能直接改数值。

## 配置说明

配置是普通 JSON（`config.json`，由 `/setup` 自动写），核心字段：

```jsonc
{
  "server":  { "host": "127.0.0.1", "port": 8777 },
  "defaults":{ "fps": 8, "quality": 80, "capture": "window", "click_mode": "event" },
  "layout":  { "min_tile_px": 260, "gap_px": 10 },
  "tiles": [
    {
      "id": "cam1",
      "title": "摄像头 1 的按钮栏",
      "window": {
        "handle": "",                 // 固定句柄（重启后会变，一般留空）
        "title_regex": ".*Camera.*",  // 按标题匹配，推荐
        "app_regex": "",              // 按进程名匹配，可叠加
        "index": 0                    // 匹配到多个时取第几个
      },
      "region": { "x": 0.0, "y": 0.0, "w": 0.6, "h": 0.12 },  // 归一化 0~1，跟窗口缩放无关
      "fps": 8,          // 抓帧帧率（0.5 ~ 30）
      "quality": 80,     // JPEG 质量（30 ~ 95）
      "capture": "window",   // window=抓窗口内容（遮挡也行）| screen=抓屏幕区域
      "interactive": true    // false = 只在页面上看，点击不回传
    }
  ]
}
```

`region` 用的是**归一化坐标**，窗口大小变了、显示器分辨率变了都能自动适配，
这也是「自适应」的关键。

## 点击回传是怎么做的

默认 `click_mode: "physical"`——用真实的鼠标事件注入（X11 用 `XTest`），兼容性最好。

关键点：**只有当目标窗口被别的窗口挡住时才会把它抬起来**。如果它本来就露在桌面上，
注入时**完全不动它的堆叠顺序，也不会闪屏**。就算需要抬起，也是「抬起来 → 点 → 立刻塞回去」，
实测约 30ms。

点完会**把鼠标指针挪回原处**，不会停在目标窗口上。

`/pins` 页面可以随时切换点击方式：

| 方式 | 特点 |
|---|---|
| **物理注入**（默认） | 真实鼠标事件，几乎所有程序都认；需要时短暂抬窗 + 指针归位 |
| 事件投递 | `XSendEvent`，完全不动物理鼠标；但**终端、部分 Electron/Qt 程序会丢弃**这类合成事件 |

> 实测：gnome-terminal 在「事件投递」下右键毫无反应，切到「物理注入」立刻弹出菜单。
> 如果你的工具点不动，先试试切换。

## 抓取模式怎么选

| 模式 | 说明 | 什么时候用 |
|---|---|---|
| `window`（默认） | 直接抓窗口自身内容：Windows 走 `PrintWindow`，X11 走 `XGetImage` | 大多数情况；窗口被遮挡/最小化也能抓 |
| `screen` | 抓窗口在屏幕上那块矩形 | `window` 模式抓到全黑时（见下） |

**X11 全黑的情况**：没有合成器（compositor）时，`XGetImage` 对被遮挡窗口拿到的是未定义内容。
改用 `"capture": "screen"` 并让窗口保持可见即可。Wayland 会话下同理。

## 平台说明

**Windows**：纯 `ctypes` 调 Win32，不需要 `pywin32`。程序启动时会把进程设为
Per-Monitor DPI Aware，避免高分屏下坐标偏移。

**Linux**：走 X11（`python-xlib` + `mss`），点击用 `XTest` 扩展注入。
需要 `DISPLAY` 环境变量；Wayland 下请用 `XWayland` 或 `screen` 模式。

## 快捷键

| 操作 | 效果 |
|---|---|
| 单击画面 | 在原窗口对应位置点击（左/中/右键都支持） |
| 滚轮 | 在原窗口滚动 |
| 双击画面 / 点 ⛶ | 放大该 tile（再双击或 `Esc` 还原） |
| 顶栏「可操作」 | 锁定后点击不再回传，纯观看 |
| 顶栏过滤框 | 按标题过滤 tile |

## 打包成可执行文件

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean frankenstein.spec
# 或者用封装好的脚本（会先清理 build/ dist/）：
python scripts/build.py
```

产物是**单文件**，直接双击/运行即可（首次运行会在旁边自动生成 `config.json`）：

| 平台 | 产物 |
|---|---|
| Linux | `dist/frankenstein` |
| Windows | `dist/frankenstein.exe` |

> PyInstaller **不支持交叉编译** —— 在 Linux 上打不出 Windows 的 exe，反之亦然。
> 仓库里带了 `.github/workflows/build.yml`，推到 GitHub 后会自动在两种原生
> runner 上各打一份，作为 artifact 下载；打 `v*` tag 时还会自动发 Release。

## 常见问题

**点击没反应？** 有些程序（游戏、部分 Electron/Qt 应用）会忽略合成事件。
Windows 上可以换成 `PostMessage` 模式（见 `src/frankenstein/platform/win32.py`
的 `post_click`）。

**帧率上不去？** 降低 `fps` 或 `quality`；tile 越多 CPU 占用越高，JPEG 编码是大头。

**想在别的机器上看？** 把 `server.host` 改成 `0.0.0.0`，然后访问 `http://<这台机器IP>:8777`。
注意：面板没有认证，不要暴露到公网。

## 目录

```
run.py                     入口 / CLI
frankenstein.spec          PyInstaller 打包配置
requirements.txt           Python 依赖
config.example.json        示例配置
config.json                你的配置（首次运行自动生成，已在 .gitignore）

src/frankenstein/          源码
  server.py                FastAPI 路由 + MJPEG + 全局热键
  capture.py               TileWorker 抓帧线程
  config.py                配置加载 / 校验 / 原子写入
  models.py                Rect / WindowInfo
  pin.py                   Snipaste 式贴图窗口
  overlay.py               全屏框选遮罩
  platform/
    base.py                后端接口
    x11.py                 Linux X11 实现
    win32.py               Windows 实现

assets/                    前端素材
  html/                    canvas / pins / setup / index
  css/                     style / canvas / setup
  js/                      canvas / pins / setup / app
  icon.svg                 应用图标

scripts/build.py           一键打包（清理 + 构建）
scripts/install.sh         安装到 ~/.local（Linux 桌面菜单项 + 图标）
build/                     构建中间物（可删，已忽略）
dist/                      生成物（可删，已忽略）
```

## 为什么界面是 HTML

界面（画布拖拽、缩放手柄、MJPEG 视频流、自适应布局）用 HTML/CSS 写，
但**用户不需要感知**：启动时自动用 Chromium 的 `--app` 模式开一个无边框窗口，
没有地址栏、没有标签页，和原生程序看起来一样。

这么选是因为一份界面代码同时覆盖 Linux 和 Windows，而且画布这类富交互
用 Web 技术实现成本远低于 Tk/Qt。后端、贴图窗口、框选遮罩都是**原生的**
（Tk + X11/Win32 API），只有主面板这一层走 Web。

如果你确实需要完全内嵌（连 Chrome 都不依赖），可以装 `pywebview`，
Linux 上还需要系统的 `gir1.2-webkit2-4.1`。
