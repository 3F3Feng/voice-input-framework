#!/usr/bin/env python3
"""
Voice Input Framework - AppKit runloop 诊断脚本
不初始化 tkinter,纯 AppKit:创建 NSStatusItem 后调用 NSApp.run() 跑 3 秒。
若图标显示 → 证明 tkinter mainloop 未驱动 Cocoa runloop 是根因;
若不显示 → 问题在图标创建/最新 macOS 的显示规则。

用法:
    python scripts/diagnose_tray_appkit.py
"""

import sys
import threading

print(f"Python: {sys.executable}")
print()

try:
    from AppKit import (
        NSApplication,
        NSImage,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from PIL import Image, ImageDraw

    print("[OK] AppKit + PIL 可用")
except ImportError as e:
    print(f"[FAIL] 依赖导入失败: {e}")
    sys.exit(1)

print()
print("=== 纯 AppKit runloop 测试(3 秒,无 tkinter)===")
print(">>> 请在菜单栏(屏幕右上角)查看是否有绿色圆形图标 <<<")

try:
    # 画绿色圆形图标
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(40, 167, 69, 255))

    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    nsimage = NSImage.alloc().initWithData_(buf.getvalue())

    app = NSApplication.sharedApplication()
    status_bar = NSStatusBar.systemStatusBar()
    status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    status_item.button().setImage_(nsimage)
    status_item.button().setToolTip_("VIF AppKit 诊断")
    print(f"[OK] status item 已创建: {status_item}")
    print(f"[OK] NSApplication: {app}")

    # 3 秒后停止 AppKit runloop
    def _stop():
        app.stop_(app)

    threading.Timer(3.0, _stop).start()

    # 跑真正的 AppKit runloop(阻塞)
    app.run()
    print("=== AppKit runloop 结束 ===")
    status_bar.removeStatusItem_(status_item)
    print(">>> 若 3 秒内看到了绿色圆形图标 → tkinter 未驱动 Cocoa runloop 是根因 <<<")
    print(">>> 若没看到 → 图标创建/最新 macOS 显示规则问题,需进一步排查 <<<")
except Exception as e:
    print(f"[FAIL] 测试失败: {e}")
    import traceback

    traceback.print_exc()
