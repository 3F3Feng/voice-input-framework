"""
Voice Input Framework - Client Library

官方 GUI 客户端和工具库
"""

# 单一版本定义来源 - 其他模块从这里导入
__version__ = "1.1.5"
__author__ = "Voice Input Framework"

# 延迟导入避免测试时触发 GUI 依赖
def __getattr__(name):
    if name == "HotkeyVoiceInput":
        from .gui import HotkeyVoiceInputV2 as HotkeyVoiceInput
        return HotkeyVoiceInput
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["HotkeyVoiceInput"]
