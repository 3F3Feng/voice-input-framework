#!/usr/bin/env python3
"""Voice Input Framework - 服务端模型模块

包含所有 STT 模型的实现。
"""

from server.models.base import BaseSTTEngine, STTEngineError
from server.models.whisper import WhisperEngine
from server.models.qwen_asr import QwenASREngine
from server.models.qwen3_asr import Qwen3ASREngine

# 可用模型注册表
# qwen_asr 现在指向 Qwen3-ASR (更快更准)
# qwen2_audio 指向旧的 Qwen2-Audio (大模型，加载慢)
AVAILABLE_MODELS = {
    "whisper": WhisperEngine,
    "whisper-small": WhisperEngine,
    "qwen_asr": Qwen3ASREngine,      # Qwen3-ASR-1.7B (推荐)
    "qwen_asr_small": Qwen3ASREngine, # Qwen3-ASR-0.6B (更快)
    "qwen2_audio": QwenASREngine,     # Qwen2-Audio-7B (大模型，备用)
}

__all__ = [
    "BaseSTTEngine",
    "STTEngineError",
    "WhisperEngine",
    "QwenASREngine",
    "Qwen3ASREngine",
    "AVAILABLE_MODELS",
]
