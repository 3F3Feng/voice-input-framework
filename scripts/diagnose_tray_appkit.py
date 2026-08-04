#!/usr/bin/env python3
"""
Voice Input Framework - AppKit runloop 诊断脚本 v2
纯 AppKit(无 tkinter)。v1 显示图标不出现,此版检查:
1. NSImage 是否有效(isValid/size)
2. status item 是否创建成功
3. 用文字 title 代替 image(排除 image 问题)
4. 修复 3 秒自动退出(postEvent 唤醒 runloop)

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
    from PIL import Image, ImageDraw

    print("[OK] AppKit + PIL 可用")
except ImportError as e:
    print(f"[FAIL] 依赖导入失败: {e}")
    sys.exit(1)

print()
print("=== 诊断 1:NSImage 有效性 ===")

try:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(40, 167, 69, 255))

    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    data = buf.getvalue()
    print(f"[INFO] PNG 字节数: {len(data)}")

    nsimage = NSImage.alloc().initWithData_(data)
    print(f"[INFO] NSImage: {nsimage}")
    print(f"[INFO] isValid: {nsimage.isValid()}")
    print(f"[INFO] size: {nsimage.size()}")
    if not nsimage.isValid():
        print("[FAIL] NSImage 无效!PNG → NSImage 转换失败")
except Exception as e:
    print(f"[FAIL] NSImage 创建失败: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

print()
print("=== 诊断 2:status item(先试 image,再试 title)===")
print(">>> 请在菜单栏(屏幕右上角)查看 <<<")

try:
    app = NSApplication.sharedApplication()
    # 终端启动的无 bundle 进程默认 activation policy 可能禁止菜单栏显示,
    # 显式设为 Accessory(仅菜单栏图标,不占 Dock)
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    print(f"[INFO] activationPolicy 已设为 Accessory")
    status_bar = NSStatusBar.systemStatusBar()

    # 方式 A:image
    item_a = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    item_a.button().setImage_(nsimage)
    item_a.button().setToolTip_("VIF image 测试")
    print(f"[INFO] item A(带 image)已创建: {item_a}")
    print(f"[INFO]   button: {item_a.button()}")
    print(f"[INFO]   button.image: {item_a.button().image()}")

    # 方式 B:纯文字 title(不依赖 image)
    item_b = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    item_b.button().setTitle_("VIF")
    item_b.button().setToolTip_("VIF title 测试")
    print("[INFO] item B(纯文字 'VIF')已创建")

    print()
    print(">>> 3 秒内若看到 'VIF' 文字 → 是 image 问题;若全无 → 更深层 <<<")

    # 3 秒后停止并唤醒 runloop
    def _stop():
        app.stop_(app)
        # postEvent 唤醒 runloop(仅设置 flag 不会退出,需事件)
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

    app.run()
    print("=== runloop 结束(3 秒)===")
    status_bar.removeStatusItem_(item_a)
    status_bar.removeStatusItem_(item_b)
    print(">>> 结果判断:看到文字=image问题;都看到=之前是时序;都没看到=更深层 <<<")
except Exception as e:
    print(f"[FAIL] status item 测试失败: {e}")
    import traceback

    traceback.print_exc()
