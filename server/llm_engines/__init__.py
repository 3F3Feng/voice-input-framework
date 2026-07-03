#!/usr/bin/env python3
"""
Voice Input Framework - LLM 引擎模块

支持多后端的 LLM 引擎实现:
- MLX: Apple Silicon 优化 (mlx-lm)
- CUDA: NVIDIA GPU 优化 (transformers + bitsandbytes)
"""

from server.llm_engines.base import BaseLLMEngine, LLMResult, LLMEngineError

__all__ = [
    "BaseLLMEngine",
    "LLMResult",
    "LLMEngineError",
]
