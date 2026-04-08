"""
Voice Input Framework - STT 客户端

与服务端通信，支持 HTTP 和 WebSocket。
"""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional, Union

import websockets

# 简单导入，路径由 run_gui.py 设置
from protocol import (
    ErrorCode,
    MessageType,
    StreamRequest,
    StreamResponse,
)
from types import TranscriptionResult

logger = logging.getLogger(__name__)

@dataclass
class STTClientConfig:
    """STT 客户端配置"""
    server_url: str = "http://localhost:6543"
    """服务端 URL"""
    ws_url: Optional[str] = None
    """WebSocket URL（默认为 http 对应的 ws）"""
    timeout: int = 30
    """请求超时（秒）"""
    max_retries: int = 3
    """最大重试次数"""
    retry_delay: float = 1.0
    """重试间隔（秒）"""

    def __post_init__(self):
        if self.ws_url is None:
            # 自动转换 http -> ws
            self.ws_url = self.server_url.replace("http://", "ws://").replace("https://", "wss://")


class STTClient:
    """
    STT 客户端
    
    使用 HTTP REST API 与服务端通信。
    """
    
    def __init__(self, config: Optional[STTClientConfig] = None):
        self.config = config or STTClientConfig()
    
    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        model: Optional[str] = None,
    ) -> TranscriptionResult:
        """发送音频数据进行转写（同步模式）"""
        import httpx
        
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
        
        payload = {
            "audio": audio_b64,
            "language": language,
        }
        if model:
            payload["model"] = model
        
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                f"{self.config.server_url}/transcribe",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        
        return TranscriptionResult(
            text=data.get("text", ""),
            confidence=data.get("confidence", 1.0),
            language=data.get("language", "auto"),
            is_final=True,
        )
    
    async def list_models(self) -> list[dict]:
        """获取可用模型列表"""
        import httpx
        
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.get(f"{self.config.server_url}/models")
            response.raise_for_status()
            return response.json().get("models", [])


class StreamingSTTClient:
    """
    流式 STT 客户端
    
    使用 WebSocket 进行实时语音识别。
    """
    
    def __init__(
        self,
        server_url: str = "ws://localhost:6543/ws/stream",
        language: str = "auto",
        model: Optional[str] = None,
    ):
        self.ws_url = server_url
        self.language = language
        self.model = model
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
    
    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        self._ws = await websockets.connect(self.ws_url)
        
        # 发送配置
        config_msg = json.dumps({
            "type": "config",
            "language": self.language,
            "model": self.model,
        })
        await self._ws.send(config_msg)
        
        # 等待就绪
        resp = await self._ws.recv()
        data = json.loads(resp)
        
        if data.get("type") == "error":
            raise RuntimeError(f"Server error: {data.get('error')}")
        
        self._connected = True
        logger.info(f"WebSocket connected: {self.ws_url}")
    
    async def send_audio(self, audio_chunk: bytes, seq: int = 0) -> None:
        """发送音频数据"""
        if not self._connected or self._ws is None:
            raise RuntimeError("Not connected")
        
        msg = json.dumps({
            "type": "audio",
            "data": base64.b64encode(audio_chunk).decode("utf-8"),
            "seq": seq,
        })
        await self._ws.send(msg)
    
    async def receive_result(self, timeout: float = 5.0) -> Optional[dict]:
        """接收识别结果"""
        if not self._connected or self._ws is None:
            return None
        
        try:
            resp = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            return json.loads(resp)
        except asyncio.TimeoutError:
            return None
    
    async def end_stream(self) -> AsyncIterator[dict]:
        """结束流并发送结束信号"""
        if not self._connected or self._ws is None:
            return
        
        # 发送结束信号
        await self._ws.send(json.dumps({"type": "end"}))
        
        # 接收剩余结果
        while True:
            try:
                resp = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
                data = json.loads(resp)
                
                if data.get("type") == "done":
                    break
                
                if data.get("type") == "result":
                    yield data
                    
            except asyncio.TimeoutError:
                break
        
        self._connected = False
    
    async def close(self) -> None:
        """关闭连接"""
        if self._ws:
            await self._ws.close()
        self._connected = False
