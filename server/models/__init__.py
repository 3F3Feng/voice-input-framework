#!/usr/bin/env python3
"""Voice Input Framework - 服务端模型模块

STT 模型引擎实现和统一注册。
模型元数据从 shared/model_registry.py 加载（单一来源）。
"""

from server.models.base import BaseSTTEngine, STTEngineError
from server.models.whisper import WhisperEngine
from server.models.whisper_mlx import WhisperMLXEngine
from server.models.whisper_cpp import WhisperCppEngine
from server.models.qwen3_asr_mlx_native import Qwen3ASRMLXNativeEngine
from shared.model_registry import MODELS_CONFIG

# 引擎类映射：engine_type → EngineClass
# 与 MODELS_CONFIG 中的 engine 字段对应
_ENGINE_CLASSES = {
    "qwen_asr_mlx_native": Qwen3ASRMLXNativeEngine,
    "whisper_mlx": WhisperMLXEngine,
    "whisper_cpp": WhisperCppEngine,
    "whisper_turbo": WhisperEngine,
    "whisper": WhisperEngine,
    "whisper-small": WhisperEngine,
}

# 可用模型注册表：名称 → EngineClass
# 从 shared/model_registry.py 自动构建
AVAILABLE_MODELS: dict = {}
model_names = list(MODELS_CONFIG.keys())

for name, config in MODELS_CONFIG.items():
    engine_type = config.get("engine")
    if engine_type in _ENGINE_CLASSES:
        AVAILABLE_MODELS[name] = _ENGINE_CLASSES[engine_type]
    else:
        import logging
        logging.getLogger(__name__).warning(
            f"No engine class registered for engine_type '{engine_type}' (model '{name}')"
        )

__all__ = [
    "BaseSTTEngine",
    "STTEngineError",
    "WhisperEngine",
    "WhisperMLXEngine",
    "WhisperCppEngine",
    "Qwen3ASRMLXNativeEngine",
    "AVAILABLE_MODELS",
    "MODELS_CONFIG",
]
