#!/usr/bin/env python3
"""
Voice Input Framework - 原生 NSStatusItem 诊断脚本
不依赖 pystray,直接用 PyObjC 创建菜单栏图标(NSStatusItem),
验证它在 tkinter mainloop 下能否显示。

用法:
    python scripts/diagnose_tray_native.py
"""

import sys

print(f"Python: {sys.executable}")
print()

# 先 tkinter(初始化 TKApplication),再 AppKit —— 与客户端同序
try:
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    print("[OK] tkinter 已初始化")
except Exception as e:
    print(f"[FAIL] tkinter 初始化失败: {e}")
    sys.exit(1)

try:
    from AppKit import (
        NSImage,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from PIL import Image, ImageDraw

    print("[OK] AppKit + PIL 可用")
except ImportError as e:
    print(f"[FAIL] 依赖导入失败: {e}")
    print("       → python -m pip install 'pyobjc-framework-Cocoa>=9.0' Pillow")
    sys.exit(1)

print()
print("=== 原生 NSStatusItem 测试(3 秒)===")
print(">>> 请在菜单栏(屏幕右上角)查看是否有蓝色圆形图标 <<<")

try:
    # 画一个蓝色圆形图标
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(0, 123, 255, 255))

    # PIL → NSImage
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    nsimage = NSImage.alloc().initWithData_(buf.getvalue())

    # 创建 status item
    status_bar = NSStatusBar.systemStatusBar()
    status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
    status_item.button().setImage_(nsimage)
    status_item.button().setToolTip_("VIF 原生诊断")
    print(f"[OK] status item 已创建并设置图标: {status_item}")

    # 跑 tkinter mainloop 3 秒 —— 若图标出现,说明原生方案可行
    root.after(3000, root.quit)
    root.mainloop()

    print("=== 3 秒结束 ===")
    status_bar.removeStatusItem_(status_item)
    print(">>> 若看到了蓝色圆形图标,原生 NSStatusItem 方案可行 <<<")
except Exception as e:
    print(f"[FAIL] 原生 NSStatusItem 测试失败: {e}")
    import traceback

    traceback.print_exc()
