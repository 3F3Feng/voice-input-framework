"""
Voice Input Framework - 服务端模型模块
"""

from voice_input_framework.server.models.base import BaseSTTEngine, STTEngineError
from voice_input_framework.server.models.whisper import WhisperEngine
from voice_input_framework.server.models.qwen_asr import QwenASREngine

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
