#!/usr/bin/env python3
"""
Voice Input Framework - 快捷键管理模块测试
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from client.hotkey_manager import HotkeyParser, HotkeyPresets, ModifierKey


class TestHotkeyParser:
    """HotkeyParser 测试类"""
    
    def test_parse_simple_modifier_combination(self):
        """测试解析简单组合键（区分左右）"""
        modifiers, main_key = HotkeyParser.parse("left_ctrl+left_alt", distinguish_left_right=True)
        assert "left_ctrl" in modifiers
        assert "left_alt" in modifiers
        assert main_key is None
    
    def test_parse_single_modifier(self):
        """测试解析单修饰键（区分左右）"""
        modifiers, main_key = HotkeyParser.parse("left_ctrl", distinguish_left_right=True)
        assert modifiers == ["left_ctrl"]
        assert main_key is None
    
    def test_parse_modifier_with_key(self):
        """测试解析带主键的快捷键（区分左右）"""
        modifiers, main_key = HotkeyParser.parse("left_ctrl+v", distinguish_left_right=True)
        assert "left_ctrl" in modifiers
        assert main_key == "v"
    
    def test_parse_right_alt(self):
        """测试解析右 Alt 键（区分左右）"""
        modifiers, main_key = HotkeyParser.parse("right_alt", distinguish_left_right=True)
        assert modifiers == ["right_alt"]
        assert main_key is None
    
    def test_parse_f_key(self):
        """测试解析功能键"""
        modifiers, main_key = HotkeyParser.parse("f13")
        assert modifiers == []
        assert main_key == "f13"
    
    def test_parse_multiple_modifiers(self):
        """测试解析多个修饰键"""
        modifiers, main_key = HotkeyParser.parse("ctrl+shift+escape")
        # 不区分左右时，返回通用修饰键名
        assert "ctrl" in modifiers
        assert "shift" in modifiers
        assert main_key == "escape"
    
    def test_parse_invalid_input(self):
        """测试解析无效输入"""
        with pytest.raises(ValueError):
            HotkeyParser.parse("")
    
    def test_parse_none_input(self):
        """测试解析 None 输入"""
        with pytest.raises(AttributeError):
            HotkeyParser.parse(None)
    
    def test_parse_case_insensitive(self):
        """测试解析大小写不敏感"""
        modifiers1, key1 = HotkeyParser.parse("LEFT_CTRL+V", distinguish_left_right=True)
        modifiers2, key2 = HotkeyParser.parse("left_ctrl+v", distinguish_left_right=True)
        assert modifiers1 == modifiers2
        assert key1 == key2
    
    def test_parse_to_string(self):
        """测试快捷键转字符串（区分左右）"""
        original = "left_ctrl+left_alt"
        modifiers, main_key = HotkeyParser.parse(original, distinguish_left_right=True)
        stringified = HotkeyParser.to_string(modifiers, main_key)
        assert stringified == original
    
    def test_parse_single_key_no_modifier(self):
        """测试解析只有主键的快捷键"""
        modifiers, main_key = HotkeyParser.parse("v")
        assert modifiers == []
        assert main_key == "v"
    
    def test_parse_ctrl_alias(self):
        """测试 ctrl 别名"""
        modifiers, main_key = HotkeyParser.parse("ctrl+v")
        assert "ctrl" in modifiers
        assert main_key == "v"
    
    def test_parse_alt_alias(self):
        """测试 alt 别名"""
        modifiers, main_key = HotkeyParser.parse("alt+v")
        assert "alt" in modifiers
        assert main_key == "v"
    
    def test_parse_without_distinguish_left_right(self):
        """测试不区分左右时 left_ctrl 被转换为 ctrl"""
        modifiers, main_key = HotkeyParser.parse("left_ctrl", distinguish_left_right=False)
        assert modifiers == ["ctrl"]  # left_ctrl 被转换为 ctrl


class TestHotkeyPresets:
    """HotkeyPresets 测试类"""
    
    def test_get_preset(self):
        """测试获取预设快捷键"""
        default = HotkeyPresets.get_preset("default")
        assert default is not None
        assert "hotkey" in default
        assert "description" in default
        assert default["hotkey"] == "left_ctrl+left_alt"
    
    def test_get_all_presets(self):
        """测试获取所有预设"""
        presets = HotkeyPresets.get_preset_names()
        assert len(presets) >= 4  # 至少应该有4个预设
        assert "default" in presets
        assert "game_mode" in presets
        assert "left_hand" in presets
        assert "classic" in presets
    
    def test_preset_descriptions(self):
        """测试预设描述"""
        for name in HotkeyPresets.get_preset_names():
            preset = HotkeyPresets.get_preset(name)
            assert preset is not None
            assert "name" in preset
            assert "description" in preset
            # 验证描述不为空
            assert len(preset["description"]) > 0
    
    def test_get_nonexistent_preset(self):
        """测试获取不存在的预设"""
        preset = HotkeyPresets.get_preset("nonexistent")
        assert preset is None


class TestModifierKey:
    """ModifierKey 测试类"""
    
    def test_get_modifier_keys(self):
        """测试获取修饰键映射"""
        modifiers = ModifierKey.get_modifier_keys()
        
        assert len(modifiers) == 8  # 左右各4个
        
        # 验证关键修饰键存在
        assert "left_ctrl" in modifiers
        assert "right_ctrl" in modifiers
        assert "left_alt" in modifiers
        assert "right_alt" in modifiers
        assert "left_shift" in modifiers
        assert "right_shift" in modifiers
    
    def test_modifier_key_values(self):
        """测试修饰键值有效"""
        modifiers = ModifierKey.get_modifier_keys()
        
        for name, keycodes in modifiers.items():
            assert len(keycodes) > 0
            # 验证是 KeyCode 类型
            from pynput.keyboard import KeyCode
            for kc in keycodes:
                assert isinstance(kc, KeyCode)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
