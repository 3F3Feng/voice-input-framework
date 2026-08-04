#!/usr/bin/env python3
"""
Voice Input Framework - AppKit runloop 诊断脚本 v3
参考 rumps(成熟 macOS 菜单栏库)的关键实现:
- status item button 设 setHighlightMode_(True)
- app.activateIgnoringOtherApps_(True)
- 用 PyObjCTools.AppHelper.runEventLoop() 而非裸 app.run()
验证这些是否是图标不显示的原因。

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
        NSEvent,
        NSPoint,
        NSApplicationDefined,
        NSApplicationActivationPolicyAccessory,
    )
    from PyObjCTools import AppHelper
    from PIL import Image, ImageDraw

    print("[OK] AppKit + PyObjCTools + PIL 可用")
except ImportError as e:
    print(f"[FAIL] 依赖导入失败: {e}")
    print("       → python -m pip install 'pyobjc-framework-Cocoa>=9.0' Pillow")
    sys.exit(1)

print()
print("=== rumps 式 status item(3 秒)===")
print(">>> 请在菜单栏(屏幕右上角)查看是否有绿色圆形图标 <<<")

try:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(40, 167, 69, 255))

    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    nsimage = NSImage.alloc().initWithData_(buf.getvalue())
    print(f"[INFO] NSImage isValid: {nsimage.isValid()}")

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    app.activateIgnoringOtherApps_(True)  # rumps 关键点

    status_bar = NSStatusBar.systemStatusBar()
    item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    item.setHighlightMode_(True)  # rumps 关键点
    item.setImage_(nsimage)
    item.setToolTip_("VIF rumps 式测试")
    item.button().setImage_(nsimage)
    print("[OK] status item 已创建(rumps 式,highlight+activate)")

    # 3 秒后停止并唤醒 runloop
    def _stop():
        app.stop_(app)
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            NSApplicationDefined,
            NSPoint(0, 0),
            0,
            0.0,
            0,
            None,
            0,
            0,
            0,
        )
        app.postEvent_atStart_(event, False)

    threading.Timer(3.0, _stop).start()

    # rumps 用 AppHelper.runEventLoop() 而非裸 app.run()
    AppHelper.runEventLoop()
    print("=== runloop 结束(3 秒)===")
    status_bar.removeStatusItem_(item)
    print(">>> 若看到绿圆 → highlight/activate/AppHelper 是关键,照此修客户端 <<<")
    print(">>> 若没看到 → 与 rumps 无关,需查最新 macOS 或环境 <<<")
except Exception as e:
    print(f"[FAIL] 测试失败: {e}")
    import traceback

    traceback.print_exc()
