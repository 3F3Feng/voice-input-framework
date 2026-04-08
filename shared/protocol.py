"""
Voice Input Framework - 通信协议定义

定义客户端和服务端之间的通信协议。
"""

import base64
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MessageType(Enum):
    """WebSocket 消息类型"""
    AUDIO_CHUNK = "audio_chunk"
    CONTROL = "control"
    CONFIG = "config"
    TRANSCRIPTION = "transcription"
    ERROR = "error"
    STATUS = "status"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"


class ErrorCode(Enum):
    """错误码"""
    UNKNOWN_ERROR = "E1000"
    INVALID_REQUEST = "E1001"
    AUDIO_DECODE_ERROR = "E2001"
    MODEL_NOT_FOUND = "E3001"
    MODEL_LOAD_ERROR = "E3002"
    INTERNAL_ERROR = "E5001"


@dataclass
class StreamRequest:
    """流式请求消息"""
    type: MessageType
    data: Optional[bytes] = None
    config: Optional[dict] = None
    control: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {"type": self.type.value}
        if self.data is not None:
            payload["data"] = base64.b64encode(self.data).decode("utf-8")
        if self.config is not None:
            payload["config"] = self.config
        if self.control is not None:
            payload["control"] = self.control
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload)

    @classmethod
    def from_json(cls, json_str: str) -> "StreamRequest":
        d = json.loads(json_str)
        msg_type = MessageType(d["type"])
        data = None
        if "data" in d:
            data = base64.b64decode(d["data"])
        return cls(
            type=msg_type,
            data=data,
            config=d.get("config"),
            control=d.get("control"),
            metadata=d.get("metadata", {}),
        )


@dataclass
class StreamResponse:
    """流式响应消息"""
    type: MessageType
    text: Optional[str] = None
    confidence: float = 1.0
    language: str = "auto"
    is_final: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {
            "type": self.type.value,
            "confidence": self.confidence,
            "language": self.language,
            "is_final": self.is_final,
        }
        if self.text is not None:
            payload["text"] = self.text
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.error_message is not None:
            payload["error_message"] = self.error_message
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload)

    @classmethod
    def from_json(cls, json_str: str) -> "StreamResponse":
        d = json.loads(json_str)
        return cls(
            type=MessageType(d["type"]),
            text=d.get("text"),
            confidence=d.get("confidence", 1.0),
            language=d.get("language", "auto"),
            is_final=d.get("is_final", False),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            metadata=d.get("metadata", {}),
        )


# WebSocket 协议常量
WS_PING_INTERVAL = 30
WS_PING_TIMEOUT = 10
WS_MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB
API_TIMEOUT = 300
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
