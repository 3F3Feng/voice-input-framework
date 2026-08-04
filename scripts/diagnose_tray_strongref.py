#!/usr/bin/env python3
"""
Voice Input Framework - AppKit 诊断脚本 v4
假设:PyObjC 的 statusItemWithLength_ 返回 +0 对象,局部变量可能在
runloop 期间被 autorelease 池回收 → 图标消失。rumps/pystray 都用
对象属性/全局强引用保持它。

此版:全局强引用 + 打印 button().image()/length() 验证对象存活。

用法:
    python scripts/diagnose_tray_strongref.py
"""

import sys
import threading

# 全局强引用(关键:防止 autorelease 回收)
_STATUS_BAR = None
_STATUS_ITEM = None

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
    from PIL import Image, ImageDraw

    print("[OK] AppKit + PIL 可用")
except ImportError as e:
    print(f"[FAIL] 依赖导入失败: {e}")
    sys.exit(1)

print()
print("=== 全局强引用 status item(5 秒)===")
print(">>> 请在菜单栏(屏幕右上角)查看是否有橙色圆形图标 <<<")

try:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(255, 140, 0, 255))

    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    nsimage = NSImage.alloc().initWithData_(buf.getvalue())
    print(f"[INFO] NSImage isValid: {nsimage.isValid()}")

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    # 全局强引用(不是局部变量!)
    _STATUS_BAR = NSStatusBar.systemStatusBar()
    _STATUS_ITEM = _STATUS_BAR.statusItemWithLength_(NSVariableStatusItemLength)
    _STATUS_ITEM.setHighlightMode_(True)
    _STATUS_ITEM.setImage_(nsimage)
    _STATUS_ITEM.setToolTip_("VIF 强引用测试")

    # 验证对象状态
    print(f"[INFO] status item: {_STATUS_ITEM}")
    print(f"[INFO] button: {_STATUS_ITEM.button()}")
    print(f"[INFO] button.image: {_STATUS_ITEM.button().image()}")
    print(
        f"[INFO] button.image 有效: {_STATUS_ITEM.button().image().isValid() if _STATUS_ITEM.button().image() else 'None!'}"
    )
    print(f"[INFO] item.length: {_STATUS_ITEM.length()}")

    # 5 秒后停止
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

    threading.Timer(5.0, _stop).start()
    app.run()
    print("=== runloop 结束(5 秒)===")
    _STATUS_BAR.removeStatusItem_(_STATUS_ITEM)
    print(">>> 若看到橙圆 → 强引用是关键,照此修客户端 <<<")
    print(">>> 若没看到 → 非引用问题,需查 PyObjC/macOS 26 兼容性 <<<")
except Exception as e:
    print(f"[FAIL] 测试失败: {e}")
    import traceback

    traceback.print_exc()
