#!/usr/bin/env bash
# 把 Frankenstein 装到当前用户（Linux 桌面：可执行文件 + 应用菜单项 + 图标）
#
#   ./scripts/install.sh             安装
#   ./scripts/install.sh --uninstall 卸载
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

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP" "$ICON" "$BIN_DST"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
    echo "已卸载：菜单项 / 图标 / 可执行文件"
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

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Frankenstein
Name[zh_CN]=缝合怪
Comment=Stitch regions of several running windows into one control panel
Comment[zh_CN]=把多个正在运行的工具窗口的指定区域缝成一个操作面板
Exec=$BIN_DST
Icon=$BIN_NAME
Terminal=false
Categories=Utility;Development;
Keywords=window;panel;dashboard;automation;snipaste;
StartupNotify=true
EOF
chmod 644 "$DESKTOP"

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "安装完成："
echo "  可执行    $BIN_DST"
echo "  菜单项    $DESKTOP"
echo "  图标      $ICON"
if ! echo ":$PATH:" | grep -q ":$HOME/.local/bin:"; then
    echo
    echo "提示：~/.local/bin 不在 PATH 里，加一下就能直接敲 frankenstein 启动："
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
fi
echo
echo "现在可以在应用菜单里搜 “Frankenstein / 缝合怪”，或直接运行 $BIN_DST"
