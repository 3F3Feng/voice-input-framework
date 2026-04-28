#!/usr/bin/env python3
"""Voice Input Framework - 服务端模型模块

包含所有 STT 模型的实现。
"""

from server.models.base import BaseSTTEngine, STTEngineError
from server.models.whisper import WhisperEngine
from server.models.whisper_mlx import WhisperMLXEngine
from server.models.whisper_cpp import WhisperCppEngine
from server.models.qwen3_asr_mlx_native import Qwen3ASRMLXNativeEngine

# 可用模型注册表 (MLX 原生优先，Apple Silicon 推荐)
AVAILABLE_MODELS = {
    # ── MLX 原生模型 (Apple Silicon 优化，推荐) ──
    "qwen_asr_mlx_native": Qwen3ASRMLXNativeEngine,  # Qwen3-ASR-1.7B MLX 8bit (⭐ 推荐)
    "qwen_asr_mlx_native_small": Qwen3ASRMLXNativeEngine,  # Qwen3-ASR-0.6B MLX 4bit
    # ── MLX Whisper 模型 (Apple Silicon) ──
    "whisper_mlx": WhisperMLXEngine,  # MLX Whisper Large V3
    "whisper_mlx_turbo": WhisperMLXEngine,  # MLX Whisper Large V3 Turbo (快速+准确)
    "whisper_mlx_medium": WhisperMLXEngine,  # MLX Whisper Medium
    "whisper_mlx_small": WhisperMLXEngine,  # MLX Whisper Small (最快)
    # ── Transformers 模型 (通用，备选) ──
    "whisper": WhisperEngine,  # Whisper Large V3
    "whisper-small": WhisperEngine,  # Whisper Small
    "whisper_turbo": WhisperEngine,  # Whisper Large V3 Turbo
    # ── Whisper.cpp 模型 (C++ 实现) ──
    "whisper_cpp": WhisperCppEngine,  # Whisper.cpp V3 Large
    "whisper_cpp_base": WhisperCppEngine,  # Whisper.cpp V3 Base
}

__all__ = [
    "BaseSTTEngine",
    "STTEngineError",
    "WhisperEngine",
    "WhisperMLXEngine",
    "WhisperCppEngine",
    "Qwen3ASRMLXNativeEngine",
    "AVAILABLE_MODELS",
]
