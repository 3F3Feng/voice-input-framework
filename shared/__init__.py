"""
Voice Input Framework - 共享模块

包含数据类型定义和模型注册表。
"""

from .data_types import (
    AudioChunk,
    TranscriptionResult,
    ModelInfo,
    HealthStatus,
)

__all__ = [
    "AudioChunk",
    "TranscriptionResult",
    "ModelInfo",
    "HealthStatus",
]
