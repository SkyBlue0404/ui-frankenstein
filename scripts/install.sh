#!/usr/bin/env bash
# 把 Frankenstein 装到当前用户（Linux 桌面）
#
#   ./scripts/install.sh               安装（含登录自启的后台守护）
#   ./scripts/install.sh --no-autostart  安装但不设自启
#   ./scripts/install.sh --uninstall   卸载
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_NAME="frankenstein"
BIN_SRC="$ROOT/dist/$BIN_NAME"
BIN_DST="$HOME/.local/bin/$BIN_NAME"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP="$APPS_DIR/$BIN_NAME.desktop"
ICON="$ICON_DIR/$BIN_NAME.svg"
AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART="$AUTOSTART_DIR/$BIN_NAME-daemon.desktop"

NO_AUTOSTART=0
for arg in "$@"; do
    [[ "$arg" == "--no-autostart" ]] && NO_AUTOSTART=1
done

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP" "$ICON" "$AUTOSTART" "$BIN_DST"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
    pkill -f "$BIN_NAME --no-window" 2>/dev/null || true
    echo "已卸载：菜单项 / 图标 / 自启项 / 可执行文件"
    exit 0
fi

if [[ ! -x "$BIN_SRC" ]]; then
    echo "[!] 找不到可执行文件：$BIN_SRC" >&2
    echo "    先打包：python scripts/build.py" >&2
    exit 1
fi

mkdir -p "$(dirname "$BIN_DST")" "$APPS_DIR" "$ICON_DIR"
install -m 755 "$BIN_SRC" "$BIN_DST"
[[ -f "$ROOT/assets/icon.svg" ]] && install -m 644 "$ROOT/assets/icon.svg" "$ICON"

# 菜单项：点它就打开面板；后台没跑的话会顺带把服务起起来
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Frankenstein
Name[zh_CN]=缝合怪
Comment=Stitch regions of several running windows into one control panel
Comment[zh_CN]=把多个正在运行的工具窗口的指定区域缝成一个操作面板
Exec=$BIN_DST
StartupWMClass=frankenstein
Icon=$BIN_NAME
Terminal=false
Categories=Utility;Development;
Keywords=window;panel;dashboard;automation;snipaste;
StartupNotify=true
EOF
chmod 644 "$DESKTOP"

# 登录自启：后台常驻，只保留全局快捷键（像 Snipaste 那样不占屏幕）
if [[ $NO_AUTOSTART -eq 0 ]]; then
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Frankenstein Daemon
Name[zh_CN]=缝合怪（后台常驻）
Comment=Keep Frankenstein running in the background for the global hotkey
Comment[zh_CN]=后台常驻，顶部状态栏显示图标，右键可切前台 / 设快捷键 / 退出
Exec=$BIN_DST --tray
Icon=$BIN_NAME
X-GNOME-Autostart-enabled=true
Terminal=false
NoDisplay=true
EOF
    chmod 644 "$AUTOSTART"
fi

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "安装完成："
echo "  可执行    $BIN_DST"
echo "  菜单项    $DESKTOP"
echo "  图标      $ICON"
[[ $NO_AUTOSTART -eq 0 ]] && echo "  登录自启  $AUTOSTART"
echo
echo "用法："
echo "  frankenstein              启动并打开面板（后台已在跑就只弹面板）"
echo "  frankenstein --no-window  只跑后台，不弹窗口（自启用的就是这个）"
echo "  frankenstein --quit       关掉后台实例"
echo "  frankenstein --list-windows  看看有哪些窗口"
echo
if ! echo ":$PATH:" | grep -q ":$HOME/.local/bin:"; then
    echo "提示：~/.local/bin 不在 PATH 里，加一下才能直接敲 frankenstein："
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
fi
