"""
Voice Input Framework - 共享模块

包含客户端和服务端共享的类型定义和通信协议。
"""

from voice_input_framework.shared.types import (
    AudioChunk,
    TranscriptionResult,
    ErrorResponse,
    ModelInfo,
    HealthStatus,
)
from voice_input_framework.shared.protocol import (
    MessageType,
    ErrorCode,
    StreamRequest,
    StreamResponse,
)

__all__ = [
    # Types
    "AudioChunk",
    "TranscriptionResult",
    "ErrorResponse",
    "ModelInfo",
    "HealthStatus",
    # Protocol
    "MessageType",
    "ErrorCode",
    "StreamRequest",
    "StreamResponse",
]
