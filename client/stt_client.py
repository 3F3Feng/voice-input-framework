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

from voice_input_framework.shared.protocol import (
    ErrorCode,
    MessageType,
    StreamRequest,
    StreamResponse,
)
from voice_input_framework.shared.types import TranscriptionResult

logger = logging.getLogger(__name__)


@dataclass
class STTClientConfig:
    """STT 客户端配置"""
    server_url: str = "http://localhost:8765"
    """服务端 URL"""
    ws_url: Optional[str] = None
    """WebSocket URL（默认为 http 对应的 ws）"""
    timeout: int = 30
    """请求超时（秒）"""
    max_retries: int = 3
    """最大重试次数"""
    retry_delay: float = 1.0
    """重试延迟（秒）"""


class STTClientError(Exception):
    """STT 客户端错误"""
    pass


class ConnectionError(STTClientError):
    """连接错误"""
    pass


class STTClient:
    """
    STT 客户端

    负责与服务端通信，支持流式和非流式识别。

    使用示例：

        # 流式识别
        ```python
        client = STTClient(config)
        async for result in client.stream_transcribe(audio_stream):
            print(result.text)
        ```

        # 文件转写
        ```python
        result = await client.transcribe_file("audio.wav")
        print(result.text)
        ```

    Attributes:
        config: 客户端配置
    """

    def __init__(self, config: Optional[STTClientConfig] = None):
        """
        初始化 STT 客户端

        Args:
            config: 客户端配置
        """
        self.config = config or STTClientConfig()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._lock = asyncio.Lock()

        # 如果没有指定 WebSocket URL，从 HTTP URL 推导
        if not self.config.ws_url:
            http_url = self.config.server_url.rstrip("/")
            self.config.ws_url = http_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/stream"

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        if self._ws is not None:
            return

        try:
            self._ws = await websockets.connect(
                self.config.ws_url,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info(f"Connected to {self.config.ws_url}")
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise ConnectionError(f"Failed to connect to server: {e}")

    async def disconnect(self) -> None:
        """关闭 WebSocket 连接"""
        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("Disconnected from server")

    async def _ensure_connected(self) -> None:
        """确保已连接"""
        if self._ws is None:
            await self.connect()

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "auto",
        sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptionResult]:
        """
        流式转写

        边接收音频边发送，边接收转写结果。

        Args:
            audio_stream: 音频流
            language: 语言
            sample_rate: 采样率

        Yields:
            TranscriptionResult: 转写结果

        Raises:
            STTClientError: 转写失败
        """
        async with self._lock:
            await self._ensure_connected()

            try:
                # 发送开始控制消息
                start_request = StreamRequest(
                    type=MessageType.CONTROL,
                    control="start",
                    metadata={"language": language, "sample_rate": sample_rate}
                )
                await self._ws.send(start_request.to_json())

                # 发送音频流
                async for audio_chunk in audio_stream:
                    request = StreamRequest(
                        type=MessageType.AUDIO_CHUNK,
                        data=audio_chunk,
                        metadata={"sample_rate": sample_rate}
                    )
                    await self._ws.send(request.to_json())

                    # 接收响应
                    try:
                        response_text = await asyncio.wait_for(
                            self._ws.recv(),
                            timeout=self.config.timeout
                        )
                        response = StreamResponse.from_json(response_text)

                        if response.type == MessageType.TRANSCRIPTION:
                            yield TranscriptionResult(
                                text=response.text or "",
                                confidence=response.confidence,
                                language=response.language,
                                is_final=response.is_final,
                            )
                        elif response.type == MessageType.ERROR:
                            raise STTClientError(
                                f"Server error: {response.error_code} - {response.error_message}"
                            )

                    except asyncio.TimeoutError:
                        logger.warning("Timeout waiting for response")

                # 发送停止控制消息
                stop_request = StreamRequest(
                    type=MessageType.CONTROL,
                    control="stop"
                )
                await self._ws.send(stop_request.to_json())

            except websockets.ConnectionClosed:
                logger.warning("Connection closed by server")
                raise ConnectionError("Connection closed by server")
            except Exception as e:
                logger.error(f"Streaming error: {e}")
                raise STTClientError(f"Streaming failed: {e}")

    async def transcribe_file(
        self,
        file_path: str,
        language: str = "auto",
        model: Optional[str] = None
    ) -> TranscriptionResult:
        """
        文件转写（通过 REST API）

        Args:
            file_path: 音频文件路径
            language: 语言
            model: 使用的模型

        Returns:
            TranscriptionResult: 转写结果
        """
        import httpx

        # 读取文件
        with open(file_path, "rb") as f:
            file_data = f.read()

        # 构建请求
        url = f"{self.config.server_url}/transcribe"

        files = {"file": (file_path.split("/")[-1], file_data, "audio/wav")}
        data = {"language": language}
        if model:
            data["model"] = model

        # 发送请求
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            try:
                response = await client.post(url, files=files, data=data)
                response.raise_for_status()

                result = response.json()
                return TranscriptionResult(
                    text=result.get("text", ""),
                    confidence=result.get("confidence", 1.0),
                    language=result.get("language", "auto"),
                    is_final=True,
                )

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error: {e}")
                raise STTClientError(f"Transcription failed: {e.response.status_code}")
            except Exception as e:
                logger.error(f"Request error: {e}")
                raise STTClientError(f"Request failed: {e}")

    async def get_models(self) -> list[dict]:
        """
        获取可用模型列表

        Returns:
            list[dict]: 模型信息列表
        """
        import httpx

        url = f"{self.config.server_url}/models"

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Failed to get models: {e}")
                raise STTClientError(f"Failed to get models: {e}")

    async def select_model(self, model_name: str) -> bool:
        """
        选择服务端模型

        Args:
            model_name: 模型名称

        Returns:
            bool: 是否成功
        """
        import httpx

        url = f"{self.config.server_url}/models/select"

        async with httpx.AsyncClient(timeout=10) as client:
            try:
                response = await client.post(url, data={"model_name": model_name})
                response.raise_for_status()
                return True
            except Exception as e:
                logger.error(f"Failed to select model: {e}")
                return False

    async def health_check(self) -> bool:
        """
        健康检查

        Returns:
            bool: 服务是否健康
        """
        import httpx

        url = f"{self.config.server_url}/health"

        async with httpx.AsyncClient(timeout=5) as client:
            try:
                response = await client.get(url)
                return response.status_code == 200
            except Exception:
                return False

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        await self.disconnect()
