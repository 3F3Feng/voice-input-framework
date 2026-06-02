#!/usr/bin/env python3
"""Voice Input Framework - 服务端模型模块

STT 模型引擎实现和统一注册。
模型元数据从 shared/model_registry.py 加载（单一来源）。
"""

# Lazy imports - only import when needed
from server.models.base import BaseSTTEngine, STTEngineError
from shared.model_registry import MODELS_CONFIG

# 引擎类映射：engine_type → EngineClass
# 使用函数获取，避免在 import 时加载所有引擎
_ENGINE_CLASSES = None

def _get_engine_classes():
    """延迟加载引擎类映射"""
    global _ENGINE_CLASSES
    if _ENGINE_CLASSES is not None:
        return _ENGINE_CLASSES
    
    from server.models.whisper import WhisperEngine
    from server.models.whisper_mlx import WhisperMLXEngine
    from server.models.whisper_cpp import WhisperCppEngine
    from server.models.qwen3_asr_mlx_native import Qwen3ASRMLXNativeEngine
    from server.models.qwen3_asr_cuda import Qwen3ASRCudaEngine
    
    _ENGINE_CLASSES = {
        "qwen_asr_mlx_native": Qwen3ASRMLXNativeEngine,
        "qwen_asr_cuda": Qwen3ASRCudaEngine,
        "whisper_mlx": WhisperMLXEngine,
        "whisper_cpp": WhisperCppEngine,
        "whisper_turbo": WhisperEngine,
        "whisper": WhisperEngine,
        "whisper-small": WhisperEngine,
    }
    return _ENGINE_CLASSES

# 可用模型注册表：名称 → EngineClass
# 从 shared/model_registry.py 自动构建
AVAILABLE_MODELS: dict = {}

def _build_available_models():
    """构建可用模型注册表"""
    global AVAILABLE_MODELS
    if AVAILABLE_MODELS:
        return
    
    engine_classes = _get_engine_classes()
    for name, config in MODELS_CONFIG.items():
        engine_type = config.get("engine")
        if engine_type in engine_classes:
            AVAILABLE_MODELS[name] = engine_classes[engine_type]
        else:
            import logging
            logging.getLogger(__name__).warning(
                f"No engine class registered for engine_type '{engine_type}' (model '{name}')"
            )

# 延迟构建
_build_available_models()

__all__ = [
    "BaseSTTEngine",
    "STTEngineError",
    "AVAILABLE_MODELS",
    "MODELS_CONFIG",
]
