#!/usr/bin/env python3
"""
Voice Input Framework - 托盘诊断脚本 v2
验证 macOS 托盘图标的正确激活方式:
主线程创建 icon → update_menu() → visible=True → tkinter mainloop 驱动渲染。

用法:
    python scripts/diagnose_tray.py
"""

import sys

print(f"Python: {sys.executable}")
print(f"Version: {sys.version.split()[0]}")
print()

# 顺序必须与客户端一致:先 tkinter(初始化 NSApplication/TKApplication),
# 再 AppKit。反之 PyObjC 的 sharedApplication() 先创建普通 NSApplication,
# tkinter 初始化时会因缺 TKApplication 方法而崩溃(NSInvalidArgumentException)。
try:
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    print("[OK] tkinter 已初始化(TKApplication)")
except Exception as e:
    print(f"[FAIL] tkinter 初始化失败: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

# 1. AppKit 可用性(此时 NSApplication 已是 TKApplication 子类)
try:
    from AppKit import NSApplication

    nsapp = NSApplication.sharedApplication()
    print(f"[OK] AppKit 可用: {nsapp}")
except ImportError as e:
    print(f"[FAIL] AppKit 导入失败: {e}")
    print("       → 运行: python -m pip install 'pyobjc-framework-Cocoa>=9.0'")
    sys.exit(1)

# 2. pystray 后端
try:
    import pystray
    from PIL import Image, ImageDraw

    print(f"[OK] pystray 后端: {pystray.Icon.__module__}")
    if "darwin" not in pystray.Icon.__module__:
        print("     ⚠️ 不是 darwin 后端!macOS 上应该是 pystray._darwin")
except ImportError as e:
    print(f"[FAIL] pystray/PIL 导入失败: {e}")
    sys.exit(1)

# 3. 核心验证:主线程激活 + tkinter mainloop 驱动
print()
print("=== 测试:主线程激活图标 + tkinter mainloop(模拟客户端场景)===")
print(">>> 请在菜单栏(屏幕右上角)查看是否有红色圆形图标 <<<")

try:
    # 绘制红色圆形图标
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(220, 53, 69, 255))

    # 主线程创建 icon(和 tray_manager 一致)
    icon = pystray.Icon(
        "vif-diagnose",
        img,
        "VIF 诊断",
        darwin_nsapplication=NSApplication.sharedApplication(),
    )
    icon.update_menu()
    icon.visible = True
    print("[OK] 主线程已激活图标(update_menu + visible=True)")

    # 跑 tkinter mainloop 3 秒(驱动 NSApplication 渲染菜单栏图标)
    root.after(3000, root.quit)
    root.mainloop()

    print("=== 3 秒结束 ===")
    print(">>> 若刚才 3 秒内看到了红色圆形图标,说明修复有效 <<<")
    print(">>> 若没看到,问题在 tkinter 未驱动 NSApp runloop,需换方案 <<<")

    icon.stop()
except Exception as e:
    print(f"[FAIL] 测试失败: {e}")
    import traceback

    traceback.print_exc()
