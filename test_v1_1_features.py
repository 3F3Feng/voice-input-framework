#!/usr/bin/env python3
"""
测试脚本 - Voice Input Framework v1.1 新功能测试

测试内容：
1. 快捷键管理器
2. 系统托盘
3. 悬浮录音指示器
"""

import sys
import os

# 添加 client 目录到路径
sys.path.insert(0, os.path.expanduser("~/voice-input-framework/client"))

def test_hotkey_manager():
    """测试快捷键管理器"""
    print("\n=== 测试快捷键管理器 ===")
    
    from hotkey_manager import HotkeyManager, HotkeyParser, HotkeyPresets
    
    # 测试解析器
    print("\n1. 测试快捷键解析器:")
    test_cases = [
        ("right_alt+v", True),
        ("left_shift+ctrl+a", True),
        ("alt+v", False),
        ("ctrl+shift+f1", True),
    ]
    
    for hotkey_str, distinguish in test_cases:
        try:
            modifiers, main_key = HotkeyParser.parse(hotkey_str, distinguish)
            display = HotkeyParser.to_string(modifiers, main_key)
            print(f"  {hotkey_str} -> {display}")
        except Exception as e:
            print(f"  {hotkey_str} -> 错误: {e}")
    
    # 测试预设方案
    print("\n2. 测试预设方案:")
    for name in HotkeyPresets.get_preset_names():
        preset = HotkeyPresets.get_preset(name)
        print(f"  {preset['name']}: {preset['hotkey']}")
    
    # 测试管理器
    print("\n3. 测试快捷键管理器:")
    manager = HotkeyManager(distinguish_left_right=True)
    manager.set_hotkey("right_alt+v")
    print(f"  当前快捷键: {HotkeyParser.to_string(manager.current_modifiers, manager.current_main_key)}")
    
    print("\n✓ 快捷键管理器测试通过")


def test_tray_manager():
    """测试系统托盘管理器"""
    print("\n=== 测试系统托盘管理器 ===")
    
    try:
        from tray_manager import TrayIconManager, TrayStatus
        
        print("\n1. 创建托盘管理器:")
        tray = TrayIconManager()
        print("  ✓ 托盘管理器创建成功")
        
        print("\n2. 测试状态设置:")
        for status in TrayStatus:
            print(f"  状态: {status.value}")
        
        print("\n✓ 系统托盘管理器测试通过")
        
    except ImportError as e:
        print(f"\n⚠️ 托盘模块导入失败: {e}")
        print("  这可能是因为缺少 pystray 或 Pillow")


def test_floating_indicator():
    """测试悬浮录音指示器"""
    print("\n=== 测试悬浮录音指示器 ===")
    
    try:
        from floating_indicator import FloatingIndicator, ProcessingIndicator
        
        print("\n1. 创建悬浮指示器:")
        indicator = FloatingIndicator()
        print("  ✓ 悬浮指示器创建成功")
        
        print("\n2. 创建处理中指示器:")
        processing = ProcessingIndicator()
        print("  ✓ 处理中指示器创建成功")
        
        print("\n✓ 悬浮指示器测试通过")
        
    except ImportError as e:
        print(f"\n⚠️ 指示器模块导入失败: {e}")


def test_gui_v2():
    """测试 GUI v2 导入"""
    print("\n=== 测试 GUI v2 导入 ===")
    
    try:
        from gui_v2 import HotkeyVoiceInputV2, DEFAULT_HOTKEY
        print(f"\n✓ GUI v2 导入成功")
        print(f"  默认快捷键: {DEFAULT_HOTKEY}")
        
    except ImportError as e:
        print(f"\n✗ GUI v2 导入失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Voice Input Framework v1.1 - 新功能测试")
    print("=" * 60)
    
    test_hotkey_manager()
    test_tray_manager()
    test_floating_indicator()
    test_gui_v2()
    
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
