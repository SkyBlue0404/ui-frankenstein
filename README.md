# Window Dashboard

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

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run.py                       # 首次会提示复制示例配置
# 打开 http://127.0.0.1:8777/setup 添加窗口
```

也可以先看看机器上有哪些窗口，方便写正则：

```bash
python run.py --list-windows
```

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

**默认 `click_mode: "event"`——不移动你的鼠标。** 事件直接投递给目标窗口
（X11 用 `XSendEvent`，Windows 用 `PostMessage`），物理光标原地不动，
所以贴图窗口可以放在屏幕任意角落，点它不会把鼠标拽到原窗口去。

点击后会自动**还原窗口的堆叠位置和输入焦点**，所以它不会停在最前面。

> ⚠️ **一个绕不过去的限制**：Chromium / Electron 程序（VS Code、Chrome 等）
> **只处理「自己处于激活状态」时的合成事件**，不激活就完全忽略。而 X11 里
> 「激活」必然伴随一次 raise（Mutter 会强制 focus 与 raise 一致，用
> `XSetInputFocus` 会被它立刻改回去）。所以点这类程序时，目标窗口会**闪一下
> （约 200ms）再回到原位**——点击能生效，但它不会停在前台。
>
> 如果你的程序是 GTK / Qt / 传统 Win32，通常不会有这个闪烁。
> 少数自绘 UI 会彻底忽略合成事件，这时把 `click_mode` 改成 `"physical"`
> 走物理注入兜底（会把鼠标移到目标位置再点）：
>
> ```jsonc
> "defaults": { "click_mode": "physical" }
> ```

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
pyinstaller --noconfirm --clean window-dashboard.spec
```

产物是**单文件**，直接双击/运行即可（首次运行会在旁边自动生成 `config.json`）：

| 平台 | 产物 |
|---|---|
| Linux | `dist/window-dashboard` |
| Windows | `dist/window-dashboard.exe` |

> PyInstaller **不支持交叉编译** —— 在 Linux 上打不出 Windows 的 exe，反之亦然。
> 仓库里带了 `.github/workflows/build.yml`，推到 GitHub 后会自动在两种原生
> runner 上各打一份，作为 artifact 下载；打 `v*` tag 时还会自动发 Release。

## 常见问题

**点击没反应？** 有些程序（游戏、部分 Electron/Qt 应用）会忽略合成事件。
Windows 上可以换成 `PostMessage` 模式（见 `wd/platform/win32.py` 的 `post_click`）。

**帧率上不去？** 降低 `fps` 或 `quality`；tile 越多 CPU 占用越高，JPEG 编码是大头。

**想在别的机器上看？** 把 `server.host` 改成 `0.0.0.0`，然后访问 `http://<这台机器IP>:8777`。
注意：面板没有认证，不要暴露到公网。

## 目录

```
run.py                 入口 / CLI
config.json            你的配置（/setup 生成）
wd/
  server.py            FastAPI 路由 + MJPEG
  capture.py           TileWorker 抓帧线程
  config.py            配置加载 / 校验 / 原子写入
  models.py            Rect / WindowInfo
  platform/
    base.py            后端接口
    x11.py             Linux X11 实现
    win32.py           Windows 实现
  static/
    index.html         主面板
    setup.html         布局编辑器
    app.js / setup.js  前端逻辑
    style.css / setup.css
```
