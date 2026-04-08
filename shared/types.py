"""
Voice Input Framework - 共享类型定义

使用 dataclass 定义所有跨模块使用的数据类型。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass
class TranscriptionResult:
    """转写结果"""
    text: str
    """识别出的文本"""
    confidence: float = 1.0
    """置信度（0.0-1.0）"""
    language: str = "auto"
    """检测到的语言"""
    is_final: bool = False
    """是否为最终结果"""
    start_time: Optional[float] = None
    """开始时间（秒）"""
    end_time: Optional[float] = None
    """结束时间（秒）"""
    words: Optional[list] = None
    """词级时间戳"""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "language": self.language,
            "is_final": self.is_final,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "words": self.words,
            "metadata": self.metadata,
        }


@dataclass
class ErrorResponse:
    """错误响应"""
    error_code: str
    error_message: str
    details: Optional[dict] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code,
            "error_message": self.error_message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ModelInfo:
    """模型信息"""
    name: str
    description: str = ""
    supported_languages: list[str] = field(default_factory=list)
    is_loaded: bool = False
    is_default: bool = False
    model_size_mb: Optional[int] = None
    latency_ms: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "supported_languages": self.supported_languages,
            "is_loaded": self.is_loaded,
            "is_default": self.is_default,
            "model_size_mb": self.model_size_mb,
            "latency_ms": self.latency_ms,
        }


@dataclass
class HealthStatus:
    """健康状态"""
    status: str
    version: str
    uptime_seconds: float
    current_model: str
    loaded_models: list[str]
    active_connections: int = 0
    memory_usage_mb: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "version": self.version,
            "uptime_seconds": self.uptime_seconds,
            "current_model": self.current_model,
            "loaded_models": self.loaded_models,
            "active_connections": self.active_connections,
            "memory_usage_mb": self.memory_usage_mb,
        }


@dataclass
class AudioChunk:
    """音频数据块"""
    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    timestamp: Optional[float] = None
    sequence_number: int = 0
    is_final: bool = False

    @property
    def duration(self) -> float:
        bytes_per_sample = self.sample_width * self.channels
        num_samples = len(self.data) // bytes_per_sample
        return num_samples / self.sample_rate
