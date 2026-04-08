"""
Voice Input Framework - 共享模块

包含协议定义和数据类型。
"""

from .protocol import (
    ErrorCode,
    MessageType,
    StreamRequest,
    StreamResponse,
)
from .data_types import (
    AudioChunk,
    TranscriptionResult,
    ModelInfo,
    HealthStatus,
)

__all__ = [
    "ErrorCode",
    "MessageType",
    "StreamRequest",
    "StreamResponse",
    "AudioChunk",
    "TranscriptionResult",
    "ModelInfo",
    "HealthStatus",
]
