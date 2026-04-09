#!/usr/bin/env python3
"""Voice Input Framework - 服务端模型模块

包含所有 STT 模型的实现。
"""

from server.models.base import BaseSTTEngine, STTEngineError
from server.models.whisper import WhisperEngine
from server.models.qwen3_asr import Qwen3ASREngine

# 可用模型注册表
AVAILABLE_MODELS = {
    "whisper": WhisperEngine,
    "whisper-small": WhisperEngine,
    "qwen_asr": Qwen3ASREngine,       # Qwen3-ASR-1.7B (推荐)
    "qwen_asr_small": Qwen3ASREngine, # Qwen3-ASR-0.6B (更快)
}

__all__ = [
    "BaseSTTEngine",
    "STTEngineError",
    "WhisperEngine",
    "Qwen3ASREngine",
    "AVAILABLE_MODELS",
]
