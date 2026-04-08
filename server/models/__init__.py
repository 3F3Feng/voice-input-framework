"""
Voice Input Framework - 服务端模型模块

包含所有 STT 模型的实现。
"""

from server.models.base import BaseSTTEngine, STTEngineError
from server.models.whisper import WhisperEngine
from server.models.qwen_asr import QwenASREngine

# 可用模型注册表
AVAILABLE_MODELS = {
    "whisper": WhisperEngine,
    "whisper-small": WhisperEngine,
    "qwen_asr": QwenASREngine,
}

__all__ = [
    "BaseSTTEngine",
    "STTEngineError",
    "WhisperEngine",
    "QwenASREngine",
    "AVAILABLE_MODELS",
]
