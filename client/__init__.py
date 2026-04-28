"""
Voice Input Framework - Client Library

官方 GUI 客户端和工具库
"""

# 单一版本定义来源 - 其他模块从这里导入
__version__ = "1.2.0"
__author__ = "Voice Input Framework"

# 重构后的应用控制器（推荐）
from .app import VoiceInputApp

# 旧版 GUI 类（兼容性保留）
from .gui import HotkeyVoiceInputV2

# 别名
HotkeyVoiceInput = HotkeyVoiceInputV2

__all__ = ["VoiceInputApp", "HotkeyVoiceInput", "HotkeyVoiceInputV2"]
