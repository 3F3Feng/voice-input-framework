#!/usr/bin/env python3
"""
Voice Input Framework - rumps 金标准测试
运行 rumps 官方 demo(最小化)。若这个都不显示菜单栏图标,
说明是运行环境/系统问题(SSH 会话?无 GUI 权限?),与我们的代码无关。

用法:
    python scripts/diagnose_rumps_demo.py
    (5 秒后自动退出)
"""

import sys
import threading

print(f"Python: {sys.executable}")
print()

try:
    import rumps

    print(f"[OK] rumps 可用: {rumps.__file__}")
except ImportError as e:
    print(f"[FAIL] rumps 未安装: {e}")
    print("       → python -m pip install rumps")
    sys.exit(1)

print()
print("=== rumps 官方 demo(5 秒)===")
print(">>> 请在菜单栏(屏幕右上角)查看是否出现 'AwesomeApp' 文字 <<<")


class AwesomeStatusBarApp(rumps.App):
    def __init__(self):
        super().__init__("AwesomeApp")

    @rumps.clicked("Preferences")
    def prefs(self, _):
        pass

    @rumps.timer(1)
    def on_tick(self, _):
        self.title = "tick"


if __name__ == "__main__":
    app = AwesomeStatusBarApp()

    # 5 秒后退出(rumps 没有内置超时,用线程调 quit_application)
    def _quit():
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().terminate_(None)
        except Exception:
            import os

            os._exit(0)

    threading.Timer(5.0, _quit).start()
    app.run()
    print("=== rumps demo 结束(5 秒)===")
    print(">>> 若看到 'AwesomeApp'/'tick' → rumps 可用,用 rumps 重写托盘 <<<")
    print(">>> 若没看到 → 运行环境/系统问题(SSH?无 GUI?),需排查环境 <<<")
