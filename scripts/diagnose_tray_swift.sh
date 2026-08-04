#!/bin/bash
# Voice Input Framework - Swift 原生 status item 测试
# 完全绕开 Python/PyObjC,用 Swift(macOS 原生)创建菜单栏图标。
# 决定性判别:
#   - Swift 显示 → PyObjC 桥接是问题 → 客户端托盘改用 Tauri/原生
#   - Swift 不显示 → 系统/环境问题,与语言无关
#
# 用法:
#   bash scripts/diagnose_tray_swift.sh

set -e
set -o pipefail
cd "$(dirname "$0")/.."

SWIFT_SRC="/tmp/vif_tray_swift.swift"
SWIFT_BIN="/tmp/vif_tray_swift"

cat > "$SWIFT_SRC" <<'SWIFT'
import AppKit

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
item.button?.image = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: nil)
item.button?.title = "VIF"
item.button?.toolTip = "VIF Swift 测试"

print("Swift status item 已创建")

// 10 秒后退出
DispatchQueue.main.asyncAfter(deadline: .now() + 10) {
    NSApp.terminate(nil)
}

app.run()
SWIFT

echo "=== 编译 Swift ==="
if command -v swiftc >/dev/null 2>&1; then
    swiftc -o "$SWIFT_BIN" "$SWIFT_SRC" 2>&1 | head -5
else
    echo "[FAIL] swiftc 未找到。请安装 Xcode 命令行工具: xcode-select --install"
    exit 1
fi
echo "[OK] 编译成功"

echo "=== 运行 Swift status item(10 秒)==="
echo ">>> 请在菜单栏(屏幕右上角)查看是否有 'VIF' 文字 + 麦克风图标 <<<"
echo ">>> 10 秒后自动退出。若程序卡住,可用 Ctrl+C 中断 <<<"

"$SWIFT_BIN"

echo "=== Swift 测试结束 ==="
echo ">>> 若看到 VIF → PyObjC 是问题,客户端托盘用 Tauri/原生实现 <<<"
echo ">>> 若没看到 → 系统/环境问题(与 Python 无关),需检查系统 <<<"
