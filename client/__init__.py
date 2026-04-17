"""
Voice Input Framework - Client Library

官方 GUI 客户端和工具库
"""

# 单一版本定义来源 - 其他模块从这里导入
__version__ = "1.2.0"
__author__ = "Voice Input Framework"

# 直接导入（兼容 Python 3.6+）
from .gui import HotkeyVoiceInputV2

# 别名
HotkeyVoiceInput = HotkeyVoiceInputV2

__all__ = ["HotkeyVoiceInput", "HotkeyVoiceInputV2"]
