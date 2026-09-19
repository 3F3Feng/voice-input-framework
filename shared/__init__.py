"""
Voice Input Framework - 共享模块

包含数据类型定义和模型注册表。
"""

from .data_types import (
    AudioChunk,
    HealthStatus,
    ModelInfo,
    TranscriptionResult,
)

__all__ = [
    "AudioChunk",
    "HealthStatus",
    "ModelInfo",
    "TranscriptionResult",
]
