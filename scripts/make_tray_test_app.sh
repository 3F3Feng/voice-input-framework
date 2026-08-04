#!/bin/bash
# Voice Input Framework - .app bundle 托盘测试
# 把 status item 测试包装成最小 .app bundle 并启动,
# 验证"无 bundle 身份的命令行进程"是否是图标不显示的根因。
#
# 用法:
#   bash scripts/make_tray_test_app.sh
#   然后看菜单栏是否有紫色圆形图标(10 秒自动退出)

set -e
cd "$(dirname "$0")/.."

APP_NAME="VIFTrayTest"
APP_DIR="/tmp/${APP_NAME}.app"
VENV_PY="$(pwd)/.venv/bin/python"

# 1. 创建 bundle 结构
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"

# 2. Info.plist —— 给进程一个真实 bundle 身份
cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>run.sh</string>
    <key>CFBundleIdentifier</key>
    <string>com.vif.traytest</string>
    <key>CFBundleName</key>
    <string>VIFTrayTest</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST

# 3. 启动脚本 —— 调 python 显示 status item
cat > "$APP_DIR/Contents/MacOS/run.sh" <<EOF
#!/bin/bash
exec "$VENV_PY" - <<'PYEOF'
import sys, threading
sys.path.insert(0, "$(pwd)")

from AppKit import (
    NSApplication, NSImage, NSStatusBar, NSVariableStatusItemLength,
    NSApplicationActivationPolicyAccessory,
)
from PIL import Image, ImageDraw
import io, os

# 紫色圆形图标
img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
draw.ellipse((8, 8, 56, 56), fill=(150, 50, 200, 255))
buf = io.BytesIO()
img.save(buf, "PNG")
nsimage = NSImage.alloc().initWithData_(buf.getvalue())

app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

status_bar = NSStatusBar.systemStatusBar()
item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
item.setHighlightMode_(True)
item.setImage_(nsimage)
item.setToolTip_("VIF bundle 测试")

# 10 秒后退出
def _quit():
    import os
    os._exit(0)
threading.Timer(10.0, _quit).start()

app.run()
PYEOF
EOF
chmod +x "$APP_DIR/Contents/MacOS/run.sh"

echo "=== .app bundle 已生成: $APP_DIR ==="
echo ">>> 现在用 open 启动,看菜单栏是否有紫色圆形图标(10 秒自动退出)<<<"
open "$APP_DIR"
echo ">>> 已启动,请观察菜单栏(右上角)<<<"
